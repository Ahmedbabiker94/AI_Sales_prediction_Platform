-- Only a new, run-identified disposable cluster. Called explicitly by the harness.
CREATE ROLE r7_forecast_migrator LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE r7_forecast_writer LOGIN PASSWORD :'writer_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE r7_forecast_reader LOGIN PASSWORD :'reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE ALL ON DATABASE :"test_database" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE :"test_database" TO r7_forecast_migrator, r7_forecast_writer, r7_forecast_reader;
GRANT CREATE ON DATABASE :"test_database" TO r7_forecast_migrator;
COMMENT ON DATABASE :"test_database" IS :'test_identity';
