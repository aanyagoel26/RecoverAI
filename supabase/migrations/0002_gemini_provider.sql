-- RecoverAI: allow Google Gemini credentials in the vault.
-- Safe to run again on databases created with an older 0001_init.sql.
ALTER TABLE integration_credentials DROP CONSTRAINT IF EXISTS ck_cred_provider;
ALTER TABLE integration_credentials ADD CONSTRAINT ck_cred_provider
  CHECK (provider IN ('paypal_sandbox', 'paypal_live', 'anthropic', 'openai', 'gemini', 'resend', 'twilio'));