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
        # مبالغ
        gross = df['gross'].sum() if 'gross' in df.columns else 0
        net = df['net_amount'].sum() if 'net_amount' in df.columns else 0
        disc = df['discount_amount'].sum() if 'discount_amount' in df.columns else 0
        pat = df['patient_share'].sum() if 'patient_share' in df.columns else 0
        comp = df['company_share'].sum() if 'company_share' in df.columns else 0
        vat = df['vat_amount'].sum() if 'vat_amount' in df.columns else 0

        # 1 - الملخص المالي
        msg1 = f"📊 تحليل عميق - إجمالي {total} مطالبة\n\n"
        msg1 += f"💰 Gross: {gross:,.2f}\n"
        msg1 += f"💰 Net: {net:,.2f}\n"
        msg1 += f"🔻 خصم: {disc:,.2f} ({disc/gross*100:.1f}% من Gross)\n" if gross else ""
        msg1 += f"👤 تحمل المريض: {pat:,.2f} ({pat/gross*100:.1f}%)\n" if gross else ""
        msg1 += f"🏢 تحمل الشركة: {comp:,.2f}\n"
        msg1 += f"🧾 VAT: {vat:,.2f}\n"
        msg1 += f"📈 متوسط Gross: {gross/total:,.2f}\n"
        msg1 += f"📈 متوسط Net: {net/total:,.2f}\n"
        # نسبة القبول من adjudication_outcome
        if 'adjudication_outcome' in df.columns:
            vc = df['adjudication_outcome'].value_counts()
            msg1 += "\n⚖️ نتيجة التحكيم adjudication_outcome:\n"
            for k,v in vc.head(10).to_dict().items():
                msg1 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        if 'response_outcome' in df.columns:
            vc = df['response_outcome'].value_counts()
            msg1 += "\n📩 نتيجة الرد response_outcome:\n"
            for k,v in vc.head(10).to_dict().items():
                msg1 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        await update.message.reply_text(msg1)

        # 2 - الأسباب والأنواع
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

        # 3 - شركات وتأمين ومرضى
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
            msg3 += "\nحسب Policy Holder (صاحب الوثيقة):\n"
            for k,v in df['policy_holder'].value_counts().head(5).to_dict().items():
                sub = df[df['policy_holder']==k]
                msg3 += f"- {k}: {v} | Net: {sub['net_amount'].sum():,.2f}\n"
        if 'accounting_period' in df.columns:
            msg3 += "\n📅 حسب Accounting Period:\n"
            for k,v in df['accounting_period'].value_counts().head(8).to_dict().items():
                msg3 += f"- {k}: {v}\n"
        # أعلى 5 مطالبات
        if 'claim_no' in df.columns and 'gross' in df.columns:
            top = df.sort_values(by='gross', ascending=False).head(5)
            msg3 += "\n💎 أعلى 5 مطالبات Gross:\n"
            for _, r in top.iterrows():
                msg3 += f"- {r.get('claim_no','')} | {r.get('patient_name','')} | Gross {r['gross']:,.2f} | Net {r.get('net_amount',0):,.2f} | {r.get('adjudication_outcome','')} | {r.get('payer_name','')}\n"
        # أعلى 5 مرضى تكرار
        if 'patient_name' in df.columns:
            msg3 += "\n👤 أكتر 5 مرضى تكرار:\n"
            for k,v in df['patient_name'].value_counts().head(5).to_dict().items():
                msg3 += f"- {k}: {v}\n"
        await update.message.reply_text(msg3)

        # 4 - حسب الشهر من claim_date
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
