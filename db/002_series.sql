-- AI Shorts Factory — seriler (ardışık bölümler) ve lisanslı kaynak filmler. İdempotent.

-- Kaynak filmler: kamu malı / lisanslı uzun videolar (Yol 1). Dosya data/sources altında durur.
CREATE TABLE IF NOT EXISTS sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT NOT NULL,
    file_path     TEXT NOT NULL UNIQUE,         -- MEDIA_ROOT'a göre: sources/film.mp4
    duration_sec  NUMERIC(8,2) NOT NULL,
    license_id    UUID NOT NULL REFERENCES licenses(id),
    source_url    TEXT,
    segments      JSONB NOT NULL DEFAULT '[]',  -- [{"start":..,"end":..}] sahne kesimlerine göre bölüm planı
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seriler: her video bir serinin bölümüdür; bölümler sırayla üretilir ve sırayla yayınlanır.
CREATE TABLE IF NOT EXISTS series (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT NOT NULL UNIQUE,
    kind           TEXT NOT NULL CHECK (kind IN ('pd_clips', 'original')),
    description    TEXT NOT NULL DEFAULT '',
    brief          TEXT,                         -- 'original' seriler için konu/format tarifi (LLM'e verilir)
    language       TEXT NOT NULL DEFAULT 'tr',
    source_id      UUID REFERENCES sources(id),  -- 'pd_clips' seriler için
    playlist_id    TEXT,                         -- YouTube oynatma listesi (ilk yüklemede oluşturulur)
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'finished')),
    next_episode   INT NOT NULL DEFAULT 1,
    buffer_size    INT NOT NULL DEFAULT 2,       -- yayınlanmamış en fazla kaç bölüm hazır beklesin
    made_for_kids  BOOLEAN NOT NULL DEFAULT false, -- YouTube "çocuklara özel" beyanı (yasal zorunluluk; doğru seçilmeli)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind <> 'pd_clips' OR source_id IS NOT NULL)
);

ALTER TABLE videos ADD COLUMN IF NOT EXISTS series_id UUID REFERENCES series(id);
ALTER TABLE videos ADD COLUMN IF NOT EXISTS episode_number INT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS episode_summary TEXT;
-- aynı serinin aynı bölümü iki kez üretilemez
CREATE UNIQUE INDEX IF NOT EXISTS videos_series_episode ON videos (series_id, episode_number)
    WHERE series_id IS NOT NULL;
ALTER TABLE series ADD COLUMN IF NOT EXISTS made_for_kids BOOLEAN NOT NULL DEFAULT false;
