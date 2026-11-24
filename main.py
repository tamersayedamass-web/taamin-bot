# -*- coding: utf-8 -*-
import os
import json
import asyncio
import logging
import tempfile
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import google.generativeai as genai
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("claims-bot")

# ------------------------------------------------------------------
# Keep-alive (Render): بيرد على GET و HEAD
# ------------------------------------------------------------------
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


threading.Thread(target=keep_alive, daemon=True).start()

# ------------------------------------------------------------------
# الإعدادات (Environment Variables)
# ------------------------------------------------------------------
TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# IDs المسموح لها تستخدم البوت (مفصولة بفاصلة). لو فاضي = الكل مسموح.
ALLOWED = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",")
    if x.isdigit()
}
# إخفاء هوية المرضى قبل ما تتبعت للذكاء الاصطناعي (1 = مفعّل)
MASK_PATIENTS = os.environ.get("MASK_PATIENTS", "1") == "1"

MAX_PROMPT_CHARS = 60000
MAX_FILE_MB = 20  # حد تليجرام لتحميل الملفات للبوتات

SYSTEM_PROMPT = """
انت مساعد ذكاء اصطناعي شغال في قسم مطالبات التأمين الطبي في مستشفى في السعودية.
بتفهم نفيس NPHIES و FHIR و ClaimResponse و Organization.
بتتكلم بالعربي المصري البسيط، وبشكل عملي.
شغلك:
- لو اتبعتلك ملخص JSON بتاع نفيس، اشرحه: رقم المطالبة، شركة التأمين، النتيجة، سبب الرفض، كود الرفض ومعناه وازاي تصلحه.
- لو اتبعتلك شركات Organization، اعرض اسم كل شركة والترخيص بتاعها صح، متكررش الأرقام.
- لو اتبعتلك تحليل ملف Excel مطالبات، اعمل تحليل عميق: المجاميع، نسب القبول والرفض، أسباب الرفض، تحليل حسب شركة التأمين والـ TPA ونوع الطلب والشهر، أعلى المطالبات، أكتر المرضى، ونصايح عملية للفريق.
- لو سألك عن كود نفيس، اشرح معناه والحل. لو مش متأكد من المعنى بالظبط قول كده بصراحة ومتخترعش معنى.
- لو سألك سؤال عام عن التأمين والمطالبات، جاوب باحترافية.
اعتمد بس على الأرقام الموجودة في البيانات، ومتحسبش أرقام من عندك.
متردش بالفرانكو، رد بالعربي.
"""

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)
else:
    model = None


# ------------------------------------------------------------------
# أدوات مساعدة
# ------------------------------------------------------------------
async def ask_ai(user_text: str) -> str:
    if not model:
        return "لسه مظبطتش مفتاح Gemini. حط GEMINI_API_KEY في Render واعمل Redeploy."
    try:
        resp = await model.generate_content_async(user_text[:MAX_PROMPT_CHARS])
        try:
            return resp.text
        except ValueError:
            return "الذكاء الاصطناعي ما رجعش رد (ممكن يكون اتحجب). جرب تاني بصياغة مختلفة."
    except Exception as e:
        log.exception("Gemini error")
        return f"فيه مشكلة في الذكاء الاصطناعي: {e}"


async def send_long(update: Update, text: str):
    """تليجرام بيقبل 4096 حرف بس، فبنقسم عند آخر سطر جديد."""
    text = (text or "").strip() or "(مفيش رد)"
    while text:
        if len(text) <= 4000:
            chunk, text = text, ""
        else:
            cut = text.rfind("\n", 0, 4000)
            if cut < 1000:
                cut = 4000
            chunk, text = text[:cut], text[cut:].lstrip("\n")
        await update.message.reply_text(chunk)


def is_allowed(update: Update) -> bool:
    if not ALLOWED:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED)


async def deny(update: Update):
    await update.message.reply_text("مش مصرحلك تستخدم البوت ده. ابعت /id وادي الرقم للمسؤول.")


def mask_id(v) -> str:
    s = str(v)
    if not MASK_PATIENTS:
        return s
    return "***" + s[-3:] if len(s) > 3 else "***"


# ------------------------------------------------------------------
# تحليل JSON بتاع نفيس (FHIR) بالكود، والذكاء الاصطناعي بيشرح بس
# ------------------------------------------------------------------
def codings(cc):
    """CodeableConcept -> list of 'code - display'"""
    out = []
    if isinstance(cc, dict):
        for c in cc.get("coding") or []:
            if not isinstance(c, dict):
                continue
            code, disp = c.get("code"), c.get("display")
            if code:
                out.append(f"{code} - {disp}" if disp else str(code))
        if not out and cc.get("text"):
            out.append(str(cc["text"]))
    return out


def amt(a) -> str:
    if isinstance(a, dict) and a.get("value") is not None:
        return f"{a['value']} {a.get('currency', '')}".strip()
    return ""


def ref_text(r) -> str:
    if not isinstance(r, dict):
        return ""
    ident = r.get("identifier")
    ident_val = ident.get("value") if isinstance(ident, dict) else None
    return r.get("display") or ident_val or r.get("reference") or ""


def extract_resources(data):
    if isinstance(data, dict) and data.get("resourceType") == "Bundle":
        return [
            e["resource"] for e in data.get("entry") or []
            if isinstance(e, dict) and isinstance(e.get("resource"), dict)
        ]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return [data] if isinstance(data, dict) else []


def summarize_claim_response(r) -> str:
    L = ["ClaimResponse:"]
    L.append(f"- id: {r.get('id')}")
    ids = [
        str(i.get("value")) for i in r.get("identifier") or []
        if isinstance(i, dict) and i.get("value")
    ]
    if ids:
        L.append(f"- identifier: {', '.join(ids)}")
    for k in ("status", "outcome", "disposition", "created", "preAuthRef"):
        if r.get(k):
            L.append(f"- {k}: {r[k]}")
    ins = ref_text(r.get("insurer"))
    if ins:
        L.append(f"- insurer: {ins}")
    req = ref_text(r.get("request"))
    if req:
        L.append(f"- request: {req}")
    for e in r.get("error") or []:
        if isinstance(e, dict):
            seq = e.get("itemSequence")
            L.append(f"- error{f' (item {seq})' if seq else ''}: {' | '.join(codings(e.get('code')))}")
    for t in r.get("total") or []:
        if isinstance(t, dict):
            L.append(f"- total [{'/'.join(codings(t.get('category')))}]: {amt(t.get('amount'))}")
    for it in r.get("item") or []:
        if not isinstance(it, dict):
            continue
        for a in it.get("adjudication") or []:
            if not isinstance(a, dict):
                continue
            line = (
                f"- item {it.get('itemSequence')} | "
                f"{'/'.join(codings(a.get('category')))} | {amt(a.get('amount'))}"
            )
            reasons = codings(a.get("reason"))
            if reasons:
                line += " | reason: " + " ; ".join(reasons)
            L.append(line)
    for n in r.get("processNote") or []:
        if isinstance(n, dict) and n.get("text"):
            L.append(f"- note: {n['text']}")
    return "\n".join(L[:80])


def summarize_orgs(orgs) -> str:
    seen, lines = set(), []
    for o in orgs:
        name = o.get("name") or "(بدون اسم)"
        ids = sorted({
            f"{str(i.get('system', '')).rstrip('/').rsplit('/', 1)[-1]}: {i.get('value')}"
            for i in o.get("identifier") or []
            if isinstance(i, dict) and i.get("value")
        })
        key = (name, tuple(ids))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {name} | {' ; '.join(ids) if ids else 'بدون ترخيص'}")
    return f"شركات (Organization) - {len(lines)} شركة بعد حذف التكرار:\n" + "\n".join(lines[:300])


def summarize_json(data):
    resources = extract_resources(data)
    if not resources:
        return None
    types = Counter(r.get("resourceType", "?") for r in resources)
    parts = [f"أنواع الموارد في الملف: {dict(types)}"]

    claims = [r for r in resources if r.get("resourceType") == "ClaimResponse"]
    orgs = [r for r in resources if r.get("resourceType") == "Organization"]
    others = [r for r in resources if r.get("resourceType") not in ("ClaimResponse", "Organization")]

    if len(claims) > 1:
        outcomes = Counter(c.get("outcome", "?") for c in claims)
        err_codes = Counter(
            code for c in claims for e in c.get("error") or [] if isinstance(e, dict)
            for code in codings(e.get("code"))
        )
        parts.append(f"توزيع النتائج: {dict(outcomes)}")
        if err_codes:
            parts.append(f"أكتر أكواد الأخطاء: {dict(err_codes.most_common(15))}")
    for c in claims[:50]:
        parts.append(summarize_claim_response(c))
    if len(claims) > 50:
        parts.append(f"... وفيه {len(claims) - 50} مطالبة تانية مش معروضة")
    if orgs:
        parts.append(summarize_orgs(orgs))
    for r in others[:5]:
        parts.append(f"{r.get('resourceType', '?')} (خام):\n" + json.dumps(r, ensure_ascii=False)[:3000])
    return "\n\n".join(parts)


# ------------------------------------------------------------------
# تحليل Excel / CSV بالـ pandas، والذكاء الاصطناعي بيفسّر ويدّي نصايح
# ------------------------------------------------------------------
NUM_COLS = ["gross", "net_amount", "discount_amount", "patient_share", "company_share", "vat_amount"]
DIST_COLS = ["payer_name", "tpa_name", "adjudication_outcome", "response_outcome", "statuscode",
             "request_type", "request_subtype", "cancel_status"]
GROUP_COLS = ["payer_name", "tpa_name", "request_type", "request_subtype",
              "policy_holder", "accounting_period"]
PATIENT_CANDIDATES = ["patient_name", "patient_id", "patient_no", "mrn", "file_no",
                      "national_id", "iqama_no", "member_id"]
REJECT_WORDS = "reject|denied|error|refus|مرفوض"


def load_table(path: str, fname: str) -> pd.DataFrame:
    if fname.endswith(".csv"):
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
    return pd.read_excel(path)


def build_excel_summary(df: pd.DataFrame) -> str:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "", regex=False), errors="coerce")

    out = [f"عدد الصفوف: {len(df)}", f"الأعمدة: {list(df.columns)}"]
    missing = [c for c in NUM_COLS + DIST_COLS if c not in df.columns]
    if missing:
        out.append(f"أعمدة متوقعة ومش موجودة: {missing}")

    # المجاميع
    out.append("\nالمجاميع:")
    for c in NUM_COLS:
        if c in df.columns:
            out.append(f"- {c}: {df[c].sum():,.2f}")

    # تحديد المرفوض
    oc = next((c for c in ("adjudication_outcome", "response_outcome") if c in df.columns), None)
    rej = None
    if oc:
        rej = df[oc].astype(str).str.lower().str.contains(REJECT_WORDS, regex=True, na=False)
        out.append(f"\nالمطالبات المرفوضة (حسب {oc}): {int(rej.sum())} من {len(df)} = {rej.mean() * 100:.1f}%")
        out.append("ملاحظة: الرفض محسوب لو القيمة فيها reject/denied/error.")
    df["_rej"] = rej if rej is not None else False

    # التوزيعات
    for c in DIST_COLS:
        if c in df.columns:
            out.append(f"\nتوزيع {c}:\n" + df[c].value_counts(dropna=False).head(10).to_string())

    # أسباب الرفض
    if "disposition" in df.columns:
        d = df["disposition"].dropna().astype(str).str.slice(0, 150)
        if rej is not None:
            d = d[rej.reindex(d.index, fill_value=False)] if rej.any() else d
        out.append("\nأكتر أسباب (disposition):\n" + d.value_counts().head(15).to_string())

    # تحليل حسب مجموعات
    def group_table(key):
        g = df.groupby(key, dropna=False)
        t = g.size().rename("count").to_frame()
        for n in ("gross", "net_amount", "patient_share", "company_share"):
            if n in df.columns:
                t[n] = g[n].sum().round(2)
        if rej is not None:
            t["rejected"] = g["_rej"].sum().astype(int)
            t["rej_%"] = (t["rejected"] / t["count"] * 100).round(1)
        return t.sort_values("count", ascending=False)

    for gc in GROUP_COLS:
        if gc in df.columns:
            out.append(f"\nتحليل حسب {gc} (أعلى 10):\n" + group_table(gc).head(10).to_string())

    # حسب الشهر
    if "claim_date" in df.columns:
        d = pd.to_datetime(df["claim_date"], errors="coerce")
        df["_month"] = d.dt.to_period("M").astype(str)
        out.append("\nتحليل حسب شهر claim_date:\n" + group_table("_month").sort_index().to_string())

    # أعلى 5 مطالبات
    if "gross" in df.columns:
        cols = [c for c in ("claim_no", "payer_name", "request_type", "gross", "net_amount",
                            "adjudication_outcome", "disposition") if c in df.columns]
        top = df.sort_values("gross", ascending=False).head(5)[cols].copy()
        if "disposition" in top.columns:
            top["disposition"] = top["disposition"].astype(str).str.slice(0, 100)
        out.append("\nأعلى 5 مطالبات (حسب gross):\n" + top.to_string())

    # أكتر المرضى
    pc = next((c for c in PATIENT_CANDIDATES if c in df.columns), None)
    if pc:
        g = df.groupby(pc).size().rename("claims").to_frame()
        if "gross" in df.columns:
            g["gross"] = df.groupby(pc)["gross"].sum().round(2)
        g = g.sort_values("claims", ascending=False).head(5)
        g.index = [mask_id(i) for i in g.index]
        out.append(f"\nأكتر 5 مرضى (من عمود {pc}{' - مخفي جزئيا' if MASK_PATIENTS else ''}):\n" + g.to_string())

    # مطالبات مكررة
    if "claim_no" in df.columns:
        dup = int(df["claim_no"].duplicated().sum())
        if dup:
            out.append(f"\nتنبيه: فيه {dup} صف مكرر في claim_no")

    return "\n".join(out)


# ------------------------------------------------------------------
# الأوامر والـ Handlers
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)
    await update.message.reply_text(
        "أهلا بيك يا باشا 🩺\n\n"
        "أنا بوت ذكاء اصطناعي لمطالبات التأمين 🤖\n"
        "ابعتلي أي حاجة وافهمها:\n"
        "1. ملف JSON بتاع نفيس (ClaimResponse / Organization)\n"
        "2. ملف Excel أو CSV بتاع المطالبات\n"
        "3. /code 1657 عشان تعرف معنى كود نفيس\n"
        "4. أي سؤال عن التأمين والمطالبات"
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"الـ ID بتاعك: {update.effective_user.id}")


async def code_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)
    code = " ".join(context.args).strip()
    if not code:
        return await update.message.reply_text("اكتب الكود بعد الأمر، مثال:\n/code 1657")
    await update.message.chat.send_action(ChatAction.TYPING)
    answer = await ask_ai(
        f"اشرح كود نفيس/رفض رقم ({code}): معناه، أسبابه الشائعة، وازاي أصلحه وأعيد تقديم المطالبة. "
        "لو مش متأكد من المعنى بالظبط قول كده ونصحني أراجع دليل نفيس الرسمي."
    )
    await send_long(update, answer)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)
    text = update.message.text or ""
    await update.message.chat.send_action(ChatAction.TYPING)
    answer = await ask_ai(text)
    await send_long(update, answer)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)

    doc = update.message.document
    fname = (doc.file_name or "").lower()

    if doc.file_size and doc.file_size > MAX_FILE_MB * 1024 * 1024:
        return await update.message.reply_text(f"الملف أكبر من {MAX_FILE_MB}MB. قسّمه لملفات أصغر.")
    if not fname.endswith((".json", ".xlsx", ".xls", ".csv")):
        return await update.message.reply_text("بقبل بس: JSON أو XLSX أو XLS أو CSV.")

    await update.message.reply_text("وصلني الملف... بحلله ⏳")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        file = await doc.get_file()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "upload" + os.path.splitext(fname)[1])
            await file.download_to_drive(custom_path=path)

            if fname.endswith(".json"):
                def read_json():
                    with open(path, encoding="utf-8-sig") as f:
                        return json.load(f)

                try:
                    data = await asyncio.to_thread(read_json)
                except json.JSONDecodeError as e:
                    return await update.message.reply_text(f"الملف مش JSON سليم: {e}")

                summary = summarize_json(data)
                if summary is None:
                    summary = json.dumps(data, ensure_ascii=False)[:20000]
                prompt = (
                    "ده ملخص اتطلع بالكود من ملف JSON بتاع نفيس. اشرحه بالتفصيل: "
                    "رقم المطالبة، شركة التأمين، النتيجة، أسباب الرفض وأكوادها ومعناها وازاي تتصلح. "
                    "ولو الملف فيه شركات Organization اعرض الاسم والترخيص من غير تكرار.\n\n" + summary
                )
                answer = await ask_ai(prompt)
                return await send_long(update, answer)

            # Excel / CSV
            def analyze():
                df = load_table(path, fname)
                return build_excel_summary(df)

            summary = await asyncio.to_thread(analyze)

        prompt = (
            "ده تحليل اتحسب بالكود لملف مطالبات تأمين. اعمل تقرير عميق بالعربي: "
            "أهم الأرقام، نسب القبول والرفض، أسباب الرفض الأساسية، أكتر شركات ومشاكل، "
            "ملاحظات على الشهور، ونصايح عملية للفريق. استخدم الأرقام الموجودة بس.\n\n" + summary
        )
        answer = await ask_ai(prompt)
        await send_long(update, answer)

    except Exception as e:
        log.exception("File handling error")
        await update.message.reply_text(f"فيه مشكلة في الملف: {e}")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN (أو TELEGRAM_TOKEN) مش متظبط في Environment Variables")
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("code", code_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(on_error)
    log.info("AI Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
