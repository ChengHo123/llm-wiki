-- Create litellm DB on first postgres boot (wiki DB auto-created by POSTGRES_DB)
SELECT 'CREATE DATABASE litellm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec
