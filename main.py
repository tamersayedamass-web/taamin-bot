def parse_fhir(text):
    try:
        data = json.loads(text)
    except:
        return None
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
            # الاسم الحقيقي
            name = o.get("name") or ""
            if not name and o.get("alias"):
                name = ", ".join(o["alias"])
            # كل المعرفات بشكل نضيف من غير تكرار
            parts = []
            for ident in o.get("identifier", []):
                val = ident.get("value", "")
                sys = ident.get("system", "")
                # نوع المعرف
                typ = ""
                if isinstance(ident.get("type"), dict):
                    cod = ident["type"].get("coding", [{}])
                    typ = cod[0].get("code", "") or cod[0].get("display", "")
                    if not typ and ident["type"].get("text"):
                        typ = ident["type"]["text"]
                label = typ or sys.split("/")[-1] or "معرف"
                if val and val not in parts:
                    parts.append(f"{label}: {val}")
            display_name = name if name else (parts[0] if parts else "شركة بدون اسم")
            lines.append(f"- {display_name}\n  {' | '.join(parts)}")
        return {"type": "orgs", "text": "\n".join(lines), "count": len(orgs)}
    return {"type": "unknown", "count": len(resources), "types": ", ".join(set([r.get("resourceType", "?") for r in resources]))}

def find_col(df, keywords):
    # بيدور على العمود بأي اسم قريب
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
    # 1. ملف JSON
    if fname.endswith(".json"):
        await update.message.reply_text("بقرا ملف الـ JSON... ثواني ⏳")
        await file.download_to_drive("input.json")
        try:
            with open("input.json", encoding="utf-8") as f:
                text = f.read()
            result = parse_fhir(text)
            if result and result.get("type") == "claim":
                info = NPHIES_CODES.get(result["code"], {"ar": "كود جديد", "fix": "هيتم تسجيله"}) if result["code"] else {"ar": "-", "fix": "-"}
                await update.message.reply_text(f"📋 تحليل المطالبة:\n\nرقم المطالبة: {result['claim_no']}\nشركة التأمين: {result.get('payer','')}\nالنتيجة: {result['outcome']}\nالسبب: {result['disposition']}\nكود نفيس: {result['code']}\nمعناه: {info['ar']}\nالحل: {info['fix']}")
            elif result and result.get("type") == "orgs":
                await update.message.reply_text(f"🏢 لقيت {result['count']} شركة تأمين:\n\n{result['text']}")
            else:
                await update.message.reply_text("فهمت الملف بس نوعه مش ClaimResponse ولا Organization.")
        except Exception as e:
            await update.message.reply_text(f"فيه مشكلة في ملف JSON: {e}")
        return
    # 2. ملف Excel / CSV
    await update.message.reply_text("بقرا الملف... ثواني ⏳")
    await file.download_to_drive("claims.xlsx")
    try:
        if fname.endswith(".csv"):
            df = pd.read_csv("claims.xlsx")
        else:
            df = pd.read_excel("claims.xlsx")
        total = len(df)
        net_col = find_col(df, ["net", "صافي"])
        gross_col = find_col(df, ["gross", "إجمالي", "total", "billed", "مبلغ"])
        payer_col = find_col(df, ["payer", "insurance", "شركة", "company", "insurer"])
        disp_col = find_col(df, ["disposition", "status", "reason", "denial", "حالة", "سبب", "رفض"])
        msg = f"📊 تحليل الملف:\n\nإجمالي المطالبات: {total}\n"
        if gross_col:
            msg += f"إجمالي Gross: {df[gross_col].sum():,.2f}\n"
        if net_col:
            msg += f"إجمالي Net: {df[net_col].sum():,.2f}\n"
        if payer_col:
            msg += "\nأكتر شركات:\n"
            for k,v in df[payer_col].value_counts().head(5).to_dict().items():
                msg += f"- {k}: {v}\n"
        if disp_col:
            msg += "\nأكتر أسباب / حالات:\n"
            for k,v in df[disp_col].value_counts().head(5).to_dict().items():
                msg += f"- {k}: {v}\n"
        msg += f"\nالأعمدة اللي لقيتها: {', '.join([str(c) for c in df.columns])}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"فيه مشكلة في الملف: {e}")
