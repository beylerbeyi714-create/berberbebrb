-- Sessiz film serileri: seslendirmesiz kartlar, Türkçe altyazı dosyası, lisanslı arka plan müziği. İdempotent.
ALTER TABLE sources ADD COLUMN IF NOT EXISTS subtitle_path TEXT;           -- filmin tamamı için Türkçe .srt (sources/ altında)
ALTER TABLE series  ADD COLUMN IF NOT EXISTS narration BOOLEAN NOT NULL DEFAULT true;        -- kartlarda Türkçe seslendirme
ALTER TABLE series  ADD COLUMN IF NOT EXISTS keep_source_audio BOOLEAN NOT NULL DEFAULT true; -- filmin kendi sesi
ALTER TABLE series  ADD COLUMN IF NOT EXISTS music_path TEXT;              -- music/ altında lisanslı parça
ALTER TABLE series  ADD COLUMN IF NOT EXISTS music_license_id UUID REFERENCES licenses(id);
