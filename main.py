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
    await update.message.reply_text(f"Ahla beek ya {name}\nEnta {role}\n\nEb3atly:\n1. JSON\n2. Excel\n3. /code 1657\n4. /my_stats\n5. /team_report")

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
            payer = "men JSON"
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
                    typ = cod[0].get("code", "") or cod[0].get("display", "")
                label = typ or "ID"
                if val:
                    parts.append(f"{label}: {val}")
            display_name = name if name else (parts[0] if parts else "Sherka bedoun esm")
            lines.append(f"- {display_name} | {' | '.join(parts)}")
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
            info = NPHIES_CODES.get(code, {"ar": "Code gedid", "fix": "Haytsegel"}) if code else {"ar": "-", "fix": "-"}
            cur.execute("INSERT INTO claims (user_id, user_name, claim_no, payer, outcome, disposition, code) VALUES (?,?,?,?,?,?,?)", (user_id, name, result["claim_no"], result.get("payer", "men JSON"), result["outcome"], result["disposition"], code))
            conn.commit()
            await update.message.reply_text(f"Ta7leel el motalba:\n\nRakam: {result['claim_no']}\nSherka: {result.get('payer','')}\nNatiga: {result['outcome']}\nSabab: {result['disposition']}\nCode: {code}\nMa3nah: {info['ar']}\nEl 7al: {info['fix']}")
            return
        elif result and result.get("type") == "orgs":
            await update.message.reply_text(f"Le2eet {result['count']} sherka:\n\n{result['text']}")
            return
        else:
            await update.message.reply_text("Mesh ader afham el JSON dah, et2aked eno kamel")
            return
    await update.message.reply_text("Eb3atly JSON aw Excel aw /code rakam el code")

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ekteb keda: /code 1657")
        return
    code = context.args[0]
    info = NPHIES_CODES.get(code)
    if info:
        await update.message.reply_text(f"Code {code}\n\nMa3na: {info['ar']}\nBel English: {info['en']}\nEl 7al: {info['fix']}")
    else:
        await update.message.reply_text(f"El code {code} lesa mesh 3andi")

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
        await update.message.reply_text("Ba2ra malaf el JSON... sawany")
        await file.download_to_drive("input.json")
        try:
            with open("input.json", encoding="utf-8") as f:
                text = f.read()
            result = parse_fhir(text)
            if result and result.get("type") == "claim":
                await update.message.reply_text(f"Ta7leel: {result['claim_no']} - {result['outcome']} - {result['disposition']}")
            elif result and result.get("type") == "orgs":
                await update.message.reply_text(f"Le2eet {result['count']} sherka:\n\n{result['text']}")
            else:
                await update.message.reply_text("Fehemt el malaf bas no3o mesh ClaimResponse")
        except Exception as e:
            await update.message.reply_text(f"Fe moshkela fe malaf JSON: {e}")
        return
    await update.message.reply_text("Ba2ra el malaf... sawany")
    await file.download_to_drive("claims.xlsx")
    try:
        if fname.endswith(".csv"):
            df = pd.read_csv("claims.xlsx")
        else:
            df = pd.read_excel("claims.xlsx")
        total = len(df)
        net_col = find_col(df, ["net", "safi"])
        gross_col = find_col(df, ["gross", "total", "billed"])
        payer_col = find_col(df, ["payer", "insurance", "company", "insurer"])
        disp_col = find_col(df, ["disposition", "status", "reason", "denial"])
        msg = f"Ta7leel el malaf:\n\nEgmali el motalbat: {total}\n"
        if gross_col:
            msg += f"Egmali Gross: {df[gross_col].sum():,.2f}\n"
        if net_col:
            msg += f"Egmali Net: {df[net_col].sum():,.2f}\n"
        if payer_col:
            msg += "\nAktar sherkat:\n"
            for k,v in df[payer_col].value_counts().head(5).to_dict().items():
                msg += f"- {k}: {v}\n"
        if disp_col:
            msg += "\nAktar asbab / halat:\n"
            for k,v in df[disp_col].value_counts().head(5).to_dict().items():
                msg += f"- {k}: {v}\n"
        msg += f"\nEl a3meda: {', '.join([str(c) for c in df.columns])}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Fe moshkela fe el malaf: {e}")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = cur.execute("SELECT COUNT(*) FROM claims WHERE user_id=?", (user_id,)).fetchone()[0]
    await update.message.reply_text(f"Shoghlak: {count} motalba")

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = cur.execute("SELECT role FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or row[0] != "admin":
        await update.message.reply_text("El takreer dah lel admin bas.")
        return
    total = cur.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    members = cur.execute("SELECT user_name, COUNT(*) FROM claims GROUP BY user_name ORDER BY COUNT(*) DESC").fetchall()
    msg = f"Takreer el faree2:\n\nEgmali: {total}\n\n"
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
