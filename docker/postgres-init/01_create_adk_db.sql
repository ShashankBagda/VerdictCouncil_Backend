-- Create the ADK session database on first container boot.
-- The main app database (verdictcouncil) is already created by POSTGRES_DB env var.
SELECT 'CREATE DATABASE verdictcouncil_adk OWNER vc_dev'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'verdictcouncil_adk')\gexec
