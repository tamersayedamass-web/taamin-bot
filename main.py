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
                await update.message.reply_text("فهمت الملف بس نوعه مش ClaimResponse ولا Organization.")
        except Exception as e:
            await update.message.reply_text(f"فيه مشكلة في ملف JSON: {e}")
        return

    await update.message.reply_text("بقرا الملف... بحلل بعمق ⏳")
    await file.download_to_drive("claims.xlsx")
    try:
        df = pd.read_csv("claims.xlsx") if fname.endswith(".csv") else pd.read_excel("claims.xlsx")
        total = len(df)
        gross_col = find_col(df, ["gross"])
        net_col = find_col(df, ["net_amount", "net"])
        disc_col = find_col(df, ["discount"])
        patient_col = find_col(df, ["patient_share"])
        company_col = find_col(df, ["company_share"])
        payer_col = find_col(df, ["payer_name", "payer"])
        tpa_col = find_col(df, ["tpa_name", "tpa"])
        provider_col = find_col(df, ["provider_name", "provider"])
        visit_col = find_col(df, ["visit_type", "visit", "encounter"])
        outcome_col = find_col(df, ["response_outcome", "adjudication_outcome", "statuscode", "claim_status"])
        code_col = find_col(df, ["denial_code", "reason_code", "code", "adjudication_code"])
        claim_col = find_col(df, ["claim_no", "claim_id", "claim"])
        date_col = find_col(df, ["date", "created", "service_date"])

        def s(col):
            return df[col].sum() if col is not None and col in df.columns else 0
        def avg(col):
            return df[col].mean() if col is not None and col in df.columns else 0

        gross = s(gross_col); net = s(net_col); disc = s(disc_col)
        # 1 - الرسالة الأولى: الملخص المالي
        msg1 = f"📊 تحليل عميق - إجمالي {total} مطالبة\n\n"
        msg1 += f"💰 إجمالي Gross: {gross:,.2f}\n"
        msg1 += f"💰 إجمالي Net: {net:,.2f}\n"
        msg1 += f"🔻 إجمالي الخصم: {disc:,.2f} ({disc/gross*100:.1f}% من Gross)\n" if gross else ""
        msg1 += f"👤 تحمل المريض: {s(patient_col):,.2f}\n"
        msg1 += f"🏢 تحمل الشركة: {s(company_col):,.2f}\n"
        msg1 += f"📈 متوسط المطالبة Gross: {avg(gross_col):,.2f}\n"
        msg1 += f"📈 متوسط Net: {avg(net_col):,.2f}\n"
        if outcome_col is not None:
            vc = df[outcome_col].value_counts()
            msg1 += "\n📍 توزيع الحالات:\n"
            for k,v in vc.head(10).to_dict().items():
                msg1 += f"- {k}: {v} ({v/total*100:.1f}%)\n"
        await update.message.reply_text(msg1)

        # 2 - الرسالة الثانية: التفاصيل حسب الأبعاد
        msg2 = "🔍 تفاصيل أعمق:\n"
        for label, col in [("حسب شركة التأمين", payer_col), ("حسب الـ TPA", tpa_col), ("حسب مقدم الخدمة", provider_col), ("حسب نوع الزيارة", visit_col)]:
            if col is not None:
                msg2 += f"\n{label}:\n"
                for k,v in df[col].value_counts().head(5).to_dict().items():
                    sub = df[df[col]==k]
                    sub_net = sub[net_col].sum() if net_col is not None else 0
                    msg2 += f"- {k}: {v} مطالبة | Net: {sub_net:,.2f}\n"
        if code_col is not None:
            msg2 += "\n❌ أكتر أكواد رفض / سبب:\n"
            for k,v in df[code_col].value_counts().head(5).to_dict().items():
                info = NPHIES_CODES.get(str(k), {})
                msg2 += f"- {k} ({v}): {info.get('ar','')}\n"
        # أعلى 5 مطالبات
        if claim_col is not None and gross_col is not None:
            top = df.sort_values(by=gross_col, ascending=False).head(5)
            msg2 += "\n💎 أعلى 5 مطالبات:\n"
            for _, r in top.iterrows():
                msg2 += f"- {r[claim_col]}: Gross {r[gross_col]:,.2f} | Net {r[net_col] if net_col is not None else 0:,.2f}\n"
        await update.message.reply_text(msg2)

        # 3 - لو فيه تاريخ
        if date_col is not None:
            try:
                df['_d'] = pd.to_datetime(df[date_col], errors='coerce')
                by_month = df.groupby(df['_d'].dt.to_period('M')).size()
                msg3 = "📅 حسب الشهر:\n"
                for k,v in by_month.to_dict().items():
                    msg3 += f"- {k}: {v}\n"
                await update.message.reply_text(msg3)
            except:
                pass

    except Exception as e:
        await update.message.reply_text(f"فيه مشكلة في الملف: {e}")
