-- AI Shorts Factory — veritabanı şeması (PostgreSQL 17)
-- Bu dosya factory veritabanına uygulanır. n8n kendi veritabanını (n8n) kullanır.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- ayarlar
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value) VALUES
    ('automation_enabled',      'false'),
    ('daily_video_target',      '3'),
    ('language',                '"tr"'),
    ('categories',              '["bilim", "tarih", "teknoloji", "psikoloji", "uzay"]'),
    ('min_duration_sec',        '20'),
    ('max_duration_sec',        '45'),
    ('upload_privacy',          '"private"'),
    ('require_manual_approval', 'true'),
    ('public_publishing_enabled','false'),
    ('daily_cost_limit_usd',    '5.0'),
    ('max_retries',             '3'),
    ('content_mode',            '"original_ai"'),
    ('providers',               '{"llm": "anthropic", "image": "openai", "video": "none", "tts": "elevenlabs"}'),
    ('publish_slots_utc',       '["09:00", "14:00", "18:00"]')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------- kanallar
CREATE TABLE IF NOT EXISTS channels (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    youtube_channel_id  TEXT UNIQUE,
    language            TEXT NOT NULL DEFAULT 'tr',
    is_default          BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS channels_one_default ON channels (is_default) WHERE is_default;
INSERT INTO channels (name, is_default) SELECT 'Ana Kanal', true
WHERE NOT EXISTS (SELECT 1 FROM channels);

-- ---------------------------------------------------------------- içerik fikirleri
CREATE TABLE IF NOT EXISTS content_ideas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id    UUID REFERENCES channels(id),
    topic         TEXT NOT NULL,
    topic_hash    TEXT NOT NULL UNIQUE,          -- normalize edilmiş konunun sha256'sı: tekrar önleme
    category      TEXT,
    language      TEXT NOT NULL DEFAULT 'tr',
    angle         TEXT,
    source        TEXT,                          -- llm / manual / optimizer / trend:<url>
    score         NUMERIC(5,2) NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','scripted','rejected','archived')),
    meta          JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS content_ideas_queue ON content_ideas (status, score DESC);

-- ---------------------------------------------------------------- senaryolar
CREATE TABLE IF NOT EXISTS scripts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idea_id       UUID REFERENCES content_ideas(id),
    language      TEXT NOT NULL DEFAULT 'tr',
    hook          TEXT NOT NULL,
    body          JSONB NOT NULL,                -- {"scenes": [{"text","visual_prompt","est_sec"}]}
    full_text     TEXT NOT NULL,
    content_hash  TEXT NOT NULL UNIQUE,          -- aynı senaryonun tekrar üretilmesini engeller
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    sources       JSONB NOT NULL DEFAULT '[]',   -- bilgi kaynakları
    provider      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- videolar
CREATE TABLE IF NOT EXISTS videos (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID REFERENCES channels(id),
    script_id           UUID NOT NULL UNIQUE REFERENCES scripts(id),  -- senaryo başına tek video
    topic               TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'tr',
    content_mode        TEXT NOT NULL DEFAULT 'original_ai'
                        CHECK (content_mode IN ('original_ai','licensed_transform')),
    stage               TEXT NOT NULL DEFAULT 'scripted'
                        CHECK (stage IN ('scripted','media_pending','media_ready','voiced',
                                         'rendering','rendered','qc_passed','qc_failed',
                                         'approved','rejected','uploading','uploaded',
                                         'scheduled','published','failed')),
    file_path           TEXT,
    subtitle_path       TEXT,
    duration_sec        NUMERIC(6,2),
    cost_usd            NUMERIC(10,4) NOT NULL DEFAULT 0,
    qc_result           JSONB,
    qc_passed           BOOLEAN,
    contains_synthetic_media BOOLEAN NOT NULL DEFAULT true,
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    publish_status      TEXT NOT NULL DEFAULT 'not_uploaded'
                        CHECK (publish_status IN ('not_uploaded','private','unlisted','scheduled','public','failed')),
    youtube_video_id    TEXT UNIQUE,                                  -- aynı videonun iki kez yüklenmesini engeller
    youtube_url         TEXT,
    scheduled_at        TIMESTAMPTZ,
    published_at        TIMESTAMPTZ,
    retries             INT NOT NULL DEFAULT 0,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS videos_stage ON videos (stage);

-- ---------------------------------------------------------------- lisanslar
CREATE TABLE IF NOT EXISTS licenses (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          TEXT NOT NULL CHECK (kind IN ('ai_generated','own_work','licensed','public_domain','cc')),
    provider      TEXT,                          -- üretici servis veya hak sahibi
    license_name  TEXT NOT NULL,                 -- örn. "OpenAI Terms – output owned by user", "CC BY 4.0"
    source_url    TEXT,
    proof_url     TEXT,                          -- lisans kanıtı (mod B için zorunlu)
    attribution   TEXT,
    verified      BOOLEAN NOT NULL DEFAULT false,
    verified_by   TEXT,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- medya varlıkları
CREATE TABLE IF NOT EXISTS media_assets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id        UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    scene_index     INT,
    kind            TEXT NOT NULL CHECK (kind IN ('image','video_clip','voice','subtitle','music','final')),
    provider        TEXT NOT NULL,
    provider_job_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','processing','ready','failed')),
    prompt          TEXT,
    file_path       TEXT,
    duration_sec    NUMERIC(6,2),
    sha256          TEXT,
    cost_usd        NUMERIC(10,4) NOT NULL DEFAULT 0,
    license_id      UUID REFERENCES licenses(id),
    attempts        INT NOT NULL DEFAULT 0,
    error           TEXT,
    meta            JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (video_id, kind, scene_index)
);

-- ---------------------------------------------------------------- yayın işleri
CREATE TABLE IF NOT EXISTS publishing_jobs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id          UUID NOT NULL REFERENCES videos(id),
    platform          TEXT NOT NULL DEFAULT 'youtube',
    privacy_status    TEXT NOT NULL DEFAULT 'private',
    publish_at        TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','uploading','uploaded','failed','cancelled')),
    youtube_video_id  TEXT,
    response          JSONB,
    attempts          INT NOT NULL DEFAULT 0,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- aynı video için aynı anda tek aktif/başarılı yükleme işi
CREATE UNIQUE INDEX IF NOT EXISTS publishing_jobs_one_active
    ON publishing_jobs (video_id, platform) WHERE status IN ('pending','uploading','uploaded');

-- ---------------------------------------------------------------- analitik
CREATE TABLE IF NOT EXISTS analytics (
    id                    BIGSERIAL PRIMARY KEY,
    video_id              UUID REFERENCES videos(id),
    youtube_video_id      TEXT NOT NULL,
    day                   DATE NOT NULL,
    source_api            TEXT NOT NULL,          -- 'data_api' | 'analytics_api'
    views                 BIGINT,
    likes                 BIGINT,
    comments              BIGINT,
    estimated_minutes_watched NUMERIC,
    average_view_duration_sec NUMERIC,
    average_view_percentage   NUMERIC,
    subscribers_gained    BIGINT,
    subscribers_lost      BIGINT,
    traffic_sources       JSONB,
    retention             JSONB,
    raw                   JSONB,
    collected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (youtube_video_id, day, source_api)
);

-- ---------------------------------------------------------------- API kullanımı / maliyet
CREATE TABLE IF NOT EXISTS api_usage (
    id          BIGSERIAL PRIMARY KEY,
    provider    TEXT NOT NULL,
    operation   TEXT NOT NULL,
    video_id    UUID,
    units       NUMERIC,                         -- token, karakter, saniye, kota birimi...
    unit_type   TEXT,
    cost_usd    NUMERIC(10,4) NOT NULL DEFAULT 0,
    success     BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_usage_day ON api_usage (created_at);

-- ---------------------------------------------------------------- iş akışı çalıştırmaları
CREATE TABLE IF NOT EXISTS workflow_runs (
    id            BIGSERIAL PRIMARY KEY,
    workflow      TEXT NOT NULL,
    execution_id  TEXT,
    video_id      UUID,
    status        TEXT NOT NULL CHECK (status IN ('started','success','error','skipped')),
    detail        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- hata kayıtları
CREATE TABLE IF NOT EXISTS error_logs (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,                 -- workflow adı / servis
    video_id      UUID,
    message       TEXT NOT NULL,
    detail        JSONB NOT NULL DEFAULT '{}',
    resolved      BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- updated_at tetikleyicisi
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DO $$ DECLARE t TEXT; BEGIN
  FOREACH t IN ARRAY ARRAY['videos','media_assets','publishing_jobs'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I_touch ON %I', t, t);
    EXECUTE format('CREATE TRIGGER %I_touch BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t, t);
  END LOOP;
END $$;
