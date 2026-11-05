ALTER DATABASE StackOverflow2010 SET QUERY_STORE = ON;
ALTER DATABASE StackOverflow2010 SET QUERY_STORE (
    OPERATION_MODE = READ_WRITE, QUERY_CAPTURE_MODE = ALL, INTERVAL_LENGTH_MINUTES = 1,
    DATA_FLUSH_INTERVAL_SECONDS = 60, MAX_STORAGE_SIZE_MB = 512, SIZE_BASED_CLEANUP_MODE = AUTO,
    CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = 30), WAIT_STATS_CAPTURE_MODE = ON);
SELECT actual_state_desc, desired_state_desc, query_capture_mode_desc, interval_length_minutes, flush_interval_seconds, max_storage_size_mb, wait_stats_capture_mode_desc FROM StackOverflow2010.sys.database_query_store_options;
