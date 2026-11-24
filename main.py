import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive")
        def log_message(self, *args):
            pass
    port = int(__import__('os').environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()
import os
import sqlite3
import json
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from nphies_codes import NPHIES_CODES

TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")

conn = sqlite3.connect("team.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, user_name TEXT, claim_no TEXT, payer TEXT, outcome TEXT, disposition TEXT, code TEXT, net_amount REAL)")
conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    role = "admin" if count == 0 else "member"
    cur.execute("INSERT OR IGNORE INTO users VALUES (?,?,?)", (user_id, name, role))
    conn.commit()
    await update.message.reply_text(
        f"أهلا بيك يا {name} 🩺\nانت متسجل كـ {role}\n\n"
        "ابعتلي:\n1. JSON بتاع رد التأمين\n2. ملف Excel\n3. /code 1657 - شرح أي كود نفيس\n4. /my_stats - شغلك\n5. /team_report - تقرير الفريق (للأدمن)"
    )

def parse_fhir(text):
    try:
        data = json.loads(text)
    except:
        return None

    # لو JSON واحد مش Bundle
    resources = []
    if isinstance(data, dict):
        if "entry" in data:
            for entry in data.get("entry", []):
                if isinstance(entry, dict) and "resource" in entry:
                    resources.append(entry["resource"])
        elif "resourceType" in data:
            resources.append(data)
    elif isinstance(data, list):
        resources = data

    if not resources:
        return None

    # 1. ClaimResponse - زي القديم
    for res in resources:
        if res.get("resourceType") == "ClaimResponse":
            outcome = res.get("outcome", "")
            disposition = res.get("disposition", "")
            code = ""
            if res.get("error"):
                try:
                    code = res["error"][0].get("code", {}).get("coding", [{}])[0].get("code", "")
                except:
                    pass
            claim_no = ""
            if res.get("request"):
                claim_no = res["request"].get("identifier", {}).get("value", "")
            # اسم شركة التأمين لو موجود
            payer = "من JSON"
            if res.get("insurer"):
                payer = res["insurer"].get("display", payer)
            return {"type": "claim", "claim_no": claim_no, "payer": payer, "outcome": outcome, "disposition": disposition, "code": code}

    # 2. Organization - ده اللي انت بعته
    orgs = [r for r in resources if r.get("resourceType") == "Organization"]
    if orgs:
        lines = []
        for o in orgs[:10]:
            name = ""
            if o.get("name"):
                name = o["name"]
            elif o.get("identifier"):
                name = str(o["identifier"][0].get("value", ""))
            lic = ""
            for ident in o.get("identifier", []):
                if "license" in str(ident.get("type", "")).lower() or ident.get("system", "").endswith("license"):
                    lic = ident.get("value", "")
            lines.append(f"- {name} | ترخيص: {lic or 'غير موجود'}")
        return {"type": "orgs", "text": "\n".join(lines), "count": len(orgs)}

    # 3. Claim عادي
    for res in resources:
        if res.get("resourceType") == "Claim":
            return {"type": "claim", "claim_no": res.get("id", ""), "payer": "من JSON", "outcome": "Claim", "disposition": res.get("status", ""), "code": ""}

    return {"type": "unknown", "count": len(resources), "types": ", ".join(set([r.get("resourceType", "?") for r in resources]))}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    if text.strip().startswith("{"):
        result = parse_fhir(text)
        if result and result.get("type") == "claim":
            code = result["code"]
            info = NPHIES_CODES.get(code, {"ar": "كود جديد", "fix": "هيتم تسجيله"}) if code else {"ar": "-", "fix": "-"}
            cur.execute("INSERT INTO claims (user_id, user_name, claim_no, payer, outcome, disposition, code) VALUES (?,?,?,?,?,?,?)",
                        (user_id, name, result["claim_no"], result.get("payer", "من JSON"), result["outcome"], result["disposition"], code))
            conn.commit()
            await update.message.reply_text(
                f"📋 تحليل المطالبة:\n\nرقم المطالبة: {result['claim_no']}\nشركة التأمين: {result.get('payer','')}\nالنتيجة: {result['outcome']}\nالسبب: {result['disposition']}\nكود نفيس: {code}\nمعناه: {info['ar']}\nالحل: {info['fix']}\n\n✅ اتسجل باسم: {name}"
            )
            return
        elif result and result.get("type") == "orgs":
            await update.message.reply_text(
                f"🏢 لقيت {result['count']} شركة تأمين في الـ JSON:\n\n{result['text']}\n\nده ملف تعريف شركات مش رد مطالبة، عشان كده مفيش قبول/رفض هنا."
            )
            return
        elif result and result.get("type") == "unknown":
            await update.message.reply_text(f"فهمت الـ JSON بس نوعه {result['types']} ومش رد مطالبة. ابعتلي ClaimResponse عشان أحلله.")
            return
        else:
            await update.message.reply_text("مش قادر أفهم الـ JSON ده، اتأكد انه كامل مش متقطع")
            return
    await update.message.reply_text("ابعتلي JSON أو Excel أو /code رقم الكود")

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب كده: /code 1657")
        return
    code = context.args[0]
    info = NPHIES_CODES.get(code)
    if info:
        await update.message.reply_text(f"كود {code}\n\nالمعنى: {info['ar']}\nبالإنجليزي: {info['en']}\nالحل: {info['fix']}")
    else:
        await update.message.reply_text(f"الكود {code} لسه مش عندي")

async def handle_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("بقرا الملف... ثواني ⏳")
    file = await update.message.document.get_file()
    await file.download_to_drive("claims.xlsx")
    try:
        df = pd.read_excel("claims.xlsx")
        total = len(df)
        total_net = df['net_amount'].sum() if 'net_amount' in df.columns else 0
        total_gross = df['gross'].sum() if 'gross' in df.columns else 0
        msg = f"📊 تحليل الملف:\n\nإجمالي المطالبات: {total}\nإجمالي Gross: {total_gross:,.2f}\nإجمالي Net: {total_net:,.2f}\n"
        if 'payer_name' in df.columns:
            msg += "\nأكتر شركات:\n"
            for k,v in df['payer_name'].value_counts().head(3).to_dict().items():
                msg += f"- {k}: {v}\n"
        if 'disposition' in df.columns:
            msg += "\nأكتر أسباب رفض:\n"
            for k,v in df['disposition'].value_counts().head(5).to_dict().items():
                msg += f"- {k}: {v}\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"فيه مشكلة في الملف: {e}")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = cur.execute("SELECT COUNT(*) FROM claims WHERE user_id=?", (user_id,)).fetchone()[0]
    await update.message.reply_text(f"شغلك: {count} مطالبة")

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = cur.execute("SELECT role FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or row[0] != "admin":
        await update.message.reply_text("التقرير ده للأدمن بس.")
        return
    total = cur.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    members = cur.execute("SELECT user_name, COUNT(*) FROM claims GROUP BY user_name ORDER BY COUNT(*) DESC").fetchall()
    msg = f"📈 تقرير الفريق:\n\nإجمالي: {total}\n\n"
    for name, c in members:
        msg += f"- {name}: {c}\n"
    await update.message.reply_text(msg)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("code", handle_code))
    app.add_handler(CommandHandler("my_stats", my_stats))
    app.add_handler(CommandHandler("team_report", team_report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_excel))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
