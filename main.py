import os
import sqlite3
import json
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from nphies_codes import NPHIES_CODES

TOKEN = os.environ.get("BOT_TOKEN")

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
        for entry in data.get("entry", []):
            res = entry.get("resource", {})
            if res.get("resourceType") == "ClaimResponse":
                outcome = res.get("outcome", "")
                disposition = res.get("disposition", "")
                code = ""
                if res.get("error"):
                    code = res["error"][0].get("code", {}).get("coding", [{}])[0].get("code", "")
                claim_no = ""
                if res.get("request"):
                    claim_no = res["request"].get("identifier", {}).get("value", "")
                return {"claim_no": claim_no, "outcome": outcome, "disposition": disposition, "code": code}
    except:
        return None
    return None

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    if text.strip().startswith("{"):
        result = parse_fhir(text)
        if result:
            code = result["code"]
            info = NPHIES_CODES.get(code, {"ar": "كود جديد", "fix": "هيتم تسجيله"})
            cur.execute("INSERT INTO claims (user_id, user_name, claim_no, payer, outcome, disposition, code) VALUES (?,?,?,?,?,?,?)",
                        (user_id, name, result["claim_no"], "من JSON", result["outcome"], result["disposition"], code))
            conn.commit()
            await update.message.reply_text(
                f"📋 تحليل المطالبة:\n\nرقم المطالبة: {result['claim_no']}\nالنتيجة: {result['outcome']}\nالسبب: {result['disposition']}\nكود نفيس: {code}\nمعناه: {info['ar']}\nالحل: {info['fix']}\n\n✅ اتسجل باسم: {name}"
            )
            return
        else:
            await update.message.reply_text("مش قادر أفهم الـ JSON ده")
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
