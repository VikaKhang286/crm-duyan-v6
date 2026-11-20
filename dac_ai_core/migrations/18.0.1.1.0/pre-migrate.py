"""Một model / kết nối → nhiều model có thứ tự: model đang chọn trở thành model "Dùng" đầu tiên, giữ kết quả kiểm tra."""


def migrate(cr, version):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'dac_ai_connection' AND column_name IN ('model_id', 'verified')")
    if len(cr.fetchall()) < 2:
        return
    cr.execute("""ALTER TABLE dac_ai_model ADD COLUMN IF NOT EXISTS selected boolean,
                                         ADD COLUMN IF NOT EXISTS verified boolean,
                                         ADD COLUMN IF NOT EXISTS sequence integer""")
    cr.execute("UPDATE dac_ai_model SET sequence = 100 WHERE sequence IS NULL")
    cr.execute("""UPDATE dac_ai_model m SET selected = true, verified = COALESCE(c.verified, false), sequence = 1
                    FROM dac_ai_connection c WHERE c.model_id = m.id""")
