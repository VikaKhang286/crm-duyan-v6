"""Thêm loại link (/p/ Pancake, /r/ lời nhắc): link cũ đều là hội thoại Pancake; bỏ ràng buộc cũ unique(url_hash)."""


def migrate(cr, version):
    cr.execute("ALTER TABLE dac_short_link ADD COLUMN IF NOT EXISTS kind varchar")
    cr.execute("UPDATE dac_short_link SET kind = 'p' WHERE kind IS NULL")
    cr.execute("ALTER TABLE dac_short_link DROP CONSTRAINT IF EXISTS dac_short_link_hash_unique")
