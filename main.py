# -*- coding: utf-8 -*-
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

import os, sqlite3, json
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
    await update.message.reply_text(f"أهلا بيك يا {name} 🩺\nانت متسجل كـ {role}\n\nابعتلي:\n1. JSON بتاع رد التأمين\n2. ملف Excel\n3. /code 1657\n4. /my_stats\n5. /team_report")

def parse_fhir(text):
    try:
        data = json.loads(text)
    except:
        return None
    resources = []
    if isinstance(data, dict):
        if "entry" in data:
            for e in data.get("entry", []):
                if isinstance(e, dict) and "resource" in e:
                    resources.append(e["resource"])
        elif "resourceType" in data:
            resources.append(data)
    elif isinstance(data, list):
        resources = data
    if not resources:
        return None
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
            payer = "من JSON"
            if res.get("insurer"):
                payer = res["insurer"].get("display", payer)
            return {"type": "claim", "claim_no": claim_no, "payer": payer, "outcome": outcome, "disposition": disposition, "code": code}
    orgs = [r for r in resources if r.get("resourceType") == "Organization"]
    if orgs:
        lines = []
        for o in orgs[:10]:
            name = o.get("name") or ""
            if not name and o.get("alias"):
                name = ", ".join(o["alias"])
            parts = []
            for ident in o.get("identifier", []):
                val = ident.get("value", "")
                typ = ""
                if isinstance(ident.get("type"), dict):
                    cod = ident["type"].get("coding", [{}])
                    typ = cod[0].get("code", "") or cod[0].get("display", "") or ident["type"].get("text", "")
                label = typ or "معرف"
                if val:
                    parts.append(f"{label}: {val}")
            display_name = name if name else (parts[0] if parts else "شركة بدون اسم")
            lines.append(f"- {display_name}\n  {' | '.join(parts)}")
        return {"type": "orgs", "text": "\n".join(lines), "count": len(orgs)}
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
            cur.execute("INSERT INTO claims (user_id, user_name, claim_no, payer, outcome, disposition, code) VALUES (?,?,?,?,?,?,?)", (user_id, name, result["claim_no"], result.get("payer", "من JSON"), result["outcome"], result["disposition"], code))
            conn.commit()
            await update.message.reply_text(f"📋 تحليل المطالبة:\n\nرقم المطالبة: {result['claim_no']}\nشركة التأمين: {result.get('payer','')}\nالنتيجة: {result['outcome']}\nالسبب: {result['disposition']}\nكود نفيس: {code}\nمعناه: {info['ar']}\nالحل: {info['fix']}\n\n✅ اتسجل باسم: {name}")
            return
        elif result and result.get("type") == "orgs":
            await update.message.reply_text(f"🏢 لقيت {result['count']} شركة تأمين:\n\n{result['text']}")
            return
        else:
            await update.message.reply_text("مش قادر أفهم الـ JSON ده، اتأكد انه كامل")
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

def find_col(df, keywords):
    cols = {str(c).lower().strip(): c for c in df.columns}
    for kw in keywords:
        for low, orig in cols.items():
            if kw in low:
                return orig
    return None

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    fname = (doc.file_name or "").lower()
    file = await doc.get_file()
    if fname.endswith(".json"):
        await update.message.reply_text("بقرا ملف الـ JSON... ثواني ⏳")
        await file.download_to_drive("input.json")
        try:
            with open("input.json", encoding="utf-8") as f:
                text = f.read()
            result = parse_fhir(text)
            if result and result.get("type") == "claim":
                await update.message.reply_text(f"📋 تحليل المطالبة:\n\nرقم المطالبة: {result['claim_no']}\nشركة التأمين: {result.get('payer','')}\nالنتيجة: {result['outcome']}\nالسبب: {result['disposition']}\nكود نفيس: {result['code']}")
            elif result and result.get("type") == "orgs":
                await update.message.reply_text(f"🏢 لقيت {result['count']} شركة تأمين:\n\n{result['text']}")
            else:
                await update.message.reply_text("فهمت الملف بس نوعه مش ClaimResponse.")
        except Exception as e:
            await update.message.reply_text(f"فيه مشكلة في ملف JSON: {e}")
        return
    await update.message.reply_text("بقرا الملف... بحلل بعمق ⏳")
    await file.download_to_drive("claims.xlsx")
    try:
        df = pd.read_csv("claims.xlsx") if fname.endswith(".csv") else pd.read_excel("claims.xlsx")
        total = len(df)
        gross = df['gross'].sum() if 'gross' in df.columns else 0
        net = df['net_amount'].sum() if 'net_amount' in df.columns else 0
        disc = df['discount_amount'].sum() if 'discount_amount' in df.columns else 0
        pat = df['patient_share'].sum() if 'patient_share' in df.columns else 0
        comp = df['company_share'].sum() if 'company_share' in df.columns else 0
        vat = df['vat_amount'].sum() if 'vat_amount' in df.columns else 0
        msg1 = f"📊 تحليل عميق - إجمالي {total} مطالبة\n\n"
        msg1 += f"💰 Gross: {gross:,.2f}\n💰 Net: {net:,.2f}\n"
        msg1 += f"🔻 خصم: {disc:,.2f} ({disc/gross*100:.1f}% من Gross)\n" if gross else ""
        msg1 += f"👤 تحمل المريض: {pat:,.2f} ({pat/gross*100:.1f}%)\n" if gross else ""
        msg1 += f"🏢 تحمل الشركة: {comp:,.2f}\n🧾 VAT: {vat:,.2f}\n"
        msg1 += f"📈 متوسط Gross: {gross/total:,.2f}\n📈 متوسط Net: {net/total:,.2f}\n"
        if 'adjudication_outcome' in df.columns:
            msg1 += "\n⚖️ نتيجة التحكيم adjudication_outcome:\n"
            for k,v in df['adjudication_outcome'].value_counts().head(10).to_dict().items():
                msg1 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        if 'response_outcome' in df.columns:
            msg1 += "\n📩 نتيجة الرد response_outcome:\n"
            for k,v in df['response_outcome'].value_counts().head(10).to_dict().items():
                msg1 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        await update.message.reply_text(msg1)
        msg2 = "🔍 تفاصيل أعمق:\n"
        if 'disposition' in df.columns:
            msg2 += "\n📝 أكتر Disposition:\n"
            for k,v in df['disposition'].value_counts().head(8).to_dict().items():
                sub_net = df[df['disposition']==k]['net_amount'].sum() if 'net_amount' in df.columns else 0
                msg2 += f"- {k}: {v} | Net: {sub_net:,.2f}\n"
        if 'statuscode' in df.columns:
            msg2 += "\n📌 Statuscode:\n"
            for k,v in df['statuscode'].value_counts().head(8).to_dict().items():
                msg2 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        if 'request_type' in df.columns:
            msg2 += "\n🩺 حسب Request Type:\n"
            for k,v in df['request_type'].value_counts().head(8).to_dict().items():
                sub = df[df['request_type']==k]
                msg2 += f"- {k}: {v} | Gross: {sub['gross'].sum():,.2f} | Net: {sub['net_amount'].sum():,.2f}\n"
        if 'request_subtype' in df.columns:
            msg2 += "\n🩺 حسب Request Subtype:\n"
            for k,v in df['request_subtype'].value_counts().head(8).to_dict().items():
                msg2 += f"- {k}: {v}\n"
        if 'cancel_status' in df.columns:
            msg2 += "\n❌ Cancel Status:\n"
            for k,v in df['cancel_status'].value_counts().head(5).to_dict().items():
                msg2 += f"- {k}: {v}\n"
        await update.message.reply_text(msg2)
        msg3 = "🏢 شركات ومرضى:\n"
        if 'payer_name' in df.columns:
            msg3 += "\nحسب Payer:\n"
            for k,v in df['payer_name'].value_counts().head(5).to_dict().items():
                sub = df[df['payer_name']==k]
                msg3 += f"- {k}: {v} | Gross: {sub['gross'].sum():,.2f} | Net: {sub['net_amount'].sum():,.2f}\n"
        if 'tpa_name' in df.columns:
            msg3 += "\nحسب TPA:\n"
            for k,v in df['tpa_name'].value_counts().head(5).to_dict().items():
                msg3 += f"- {k}: {v}\n"
        if 'policy_holder' in df.columns:
            msg3 += "\nحسب Policy Holder:\n"
            for k,v in df['policy_holder'].value_counts().head(5).to_dict().items():
                sub = df[df['policy_holder']==k]
                msg3 += f"- {k}: {v} | Net: {sub['net_amount'].sum():,.2f}\n"
        if 'accounting_period' in df.columns:
            msg3 += "\n📅 حسب Accounting Period:\n"
            for k,v in df['accounting_period'].value_counts().head(8).to_dict().items():
                msg3 += f"- {k}: {v}\n"
        if 'claim_no' in df.columns and 'gross' in df.columns:
            top = df.sort_values(by='gross', ascending=False).head(5)
            msg3 += "\n💎 أعلى 5 مطالبات Gross:\n"
            for _, r in top.iterrows():
                msg3 += f"- {r.get('claim_no','')} | {r.get('patient_name','')} | Gross {r['gross']:,.2f} | Net {r.get('net_amount',0):,.2f} | {r.get('adjudication_outcome','')} | {r.get('payer_name','')}\n"
        if 'patient_name' in df.columns:
            msg3 += "\n👤 أكتر 5 مرضى تكرار:\n"
            for k,v in df['patient_name'].value_counts().head(5).to_dict().items():
                msg3 += f"- {k}: {v}\n"
        await update.message.reply_text(msg3)
        if 'claim_date' in df.columns:
            try:
                df['_d'] = pd.to_datetime(df['claim_date'], errors='coerce')
                by_m = df.groupby(df['_d'].dt.to_period('M')).agg(count=('claim_no','count'), gross=('gross','sum'), net=('net_amount','sum'))
                msg4 = "📅 حسب الشهر (claim_date):\n"
                for k, row in by_m.iterrows():
                    msg4 += f"- {k}: {int(row['count'])} مطالبة | Gross {row['gross']:,.2f} | Net {row['net']:,.2f}\n"
                await update.message.reply_text(msg4)
            except:
                pass
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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("Bot is running...")
    app.run_polling()
if __name__ == "__main__":
    main()
