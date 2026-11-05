-- Inicializacao idempotente do ambiente de experimentos.
-- Cria o banco vazio tcc_lab e liga o Query Store capturando TODAS as consultas
-- com janela de agregacao curta (1 min), para o experimento nao esperar 60 min.
SET NOCOUNT ON;
GO
IF DB_ID(N'tcc_lab') IS NULL
    CREATE DATABASE tcc_lab;
GO
ALTER DATABASE tcc_lab SET RECOVERY SIMPLE;   -- menos log nas cargas de teste
ALTER DATABASE tcc_lab SET QUERY_STORE = ON;
ALTER DATABASE tcc_lab SET QUERY_STORE (
    OPERATION_MODE = READ_WRITE,
    QUERY_CAPTURE_MODE = ALL,            -- captura inclusive consultas baratas/ad hoc
    INTERVAL_LENGTH_MINUTES = 1,         -- janela de coleta curta (padrao: 60)
    DATA_FLUSH_INTERVAL_SECONDS = 60,    -- descarrega para disco a cada 60 s (padrao: 900)
    MAX_STORAGE_SIZE_MB = 512,
    SIZE_BASED_CLEANUP_MODE = AUTO,
    CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = 30),
    WAIT_STATS_CAPTURE_MODE = ON
);
GO
SELECT name, is_query_store_on FROM sys.databases WHERE name = N'tcc_lab';
GO
