def migrate(cr, version):
    """AI từng ghi nhiều nhân viên trong một nhãn ("Tieu Bang, Dao, ?"): giữ người đầu tiên làm nhân viên chính."""
    cr.execute("""
        UPDATE dac_chat_insight
           SET staff_name = COALESCE(NULLIF(btrim(regexp_replace(staff_name, '\\s*(,|/|&|\\+|;| và ).*$', '')), ''), '?')
         WHERE staff_name ~ '(,|/|&|\\+|;| và )'
    """)
