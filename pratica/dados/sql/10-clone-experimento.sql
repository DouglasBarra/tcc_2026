-- Cria (ou recria) o clone de experimento so_exp a partir do .bak original.
-- O banco base StackOverflow2010 nunca e usado em medicao. Idempotente.
-- Uso: ./scripts/sqlcmd.sh -i /dev/stdin < ../dados/sql/10-clone-experimento.sql   (ou via experimento/preparar.sh)
SET NOCOUNT ON;
IF DB_ID('so_exp') IS NOT NULL
BEGIN
    ALTER DATABASE so_exp SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE so_exp;
END
RESTORE DATABASE so_exp FROM DISK = '/var/opt/mssql/backups/StackOverflow2010_original.bak'
    WITH MOVE 'StackOverflow2010'     TO '/var/opt/mssql/data/so_exp.mdf',
         MOVE 'StackOverflow2010_log' TO '/var/opt/mssql/data/so_exp_log.ldf',
         CHECKSUM, STATS = 50;
-- Mesmas opcoes do tcc_lab / sql/02-query-store.sql (Query Store ligado em todos os bracos).
ALTER DATABASE so_exp SET QUERY_STORE = ON;
ALTER DATABASE so_exp SET QUERY_STORE (
    OPERATION_MODE = READ_WRITE, QUERY_CAPTURE_MODE = ALL, INTERVAL_LENGTH_MINUTES = 1,
    DATA_FLUSH_INTERVAL_SECONDS = 60, MAX_STORAGE_SIZE_MB = 512, SIZE_BASED_CLEANUP_MODE = AUTO,
    CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = 30), WAIT_STATS_CAPTURE_MODE = ON);
SELECT name, compatibility_level, recovery_model_desc, is_auto_create_stats_on, is_auto_update_stats_on
FROM sys.databases WHERE name = 'so_exp';
