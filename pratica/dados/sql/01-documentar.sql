SET NOCOUNT ON;
USE StackOverflow2010;
PRINT '== versao/compat/recovery/collation';
SELECT name, compatibility_level, recovery_model_desc, collation_name, state_desc, page_verify_option_desc,
       is_auto_create_stats_on, is_auto_update_stats_on, is_auto_update_stats_async_on, is_read_committed_snapshot_on,
       snapshot_isolation_state_desc, is_query_store_on, containment_desc, target_recovery_time_in_seconds
FROM sys.databases WHERE name='StackOverflow2010';
PRINT '== arquivos';
SELECT name, type_desc, physical_name, size*8/1024 AS size_mb, growth, is_percent_growth FROM sys.database_files;
PRINT '== tabelas e contagens (COUNT_BIG por tabela, via dinamico) + paginas';
CREATE TABLE #c (tabela sysname, linhas bigint);
DECLARE @s nvarchar(max)=N'';
SELECT @s += N'INSERT #c SELECT ''' + s.name+'.'+t.name + N''', COUNT_BIG(*) FROM ' + QUOTENAME(s.name)+'.'+QUOTENAME(t.name)+N';' 
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id;
EXEC(@s);
SELECT c.tabela, c.linhas,
  (SELECT SUM(ps.used_page_count)*8/1024.0 FROM sys.dm_db_partition_stats ps WHERE ps.object_id=OBJECT_ID(c.tabela)) AS usado_mb,
  (SELECT SUM(ps.in_row_data_page_count) FROM sys.dm_db_partition_stats ps WHERE ps.object_id=OBJECT_ID(c.tabela) AND ps.index_id IN (0,1)) AS paginas_dados,
  (SELECT TOP 1 data_compression_desc FROM sys.partitions p WHERE p.object_id=OBJECT_ID(c.tabela) AND p.index_id IN (0,1)) AS compressao
FROM #c c ORDER BY c.linhas DESC;
PRINT '== views/procs/triggers/FKs/constraints';
SELECT (SELECT COUNT(*) FROM sys.views) views, (SELECT COUNT(*) FROM sys.procedures) procs, (SELECT COUNT(*) FROM sys.triggers) triggers,
       (SELECT COUNT(*) FROM sys.foreign_keys) fks, (SELECT COUNT(*) FROM sys.check_constraints) checks, (SELECT COUNT(*) FROM sys.default_constraints) defaults;
PRINT '== colunas';
SELECT t.name tabela, c.column_id, c.name coluna, ty.name tipo, c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity
FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.types ty ON ty.user_type_id=c.user_type_id
ORDER BY t.name, c.column_id;
PRINT '== TODOS os indices';
SELECT t.name tabela, i.index_id, i.name indice, i.type_desc, i.is_unique, i.is_primary_key, i.has_filter,
  STUFF((SELECT ','+c.name+CASE WHEN ic.is_descending_key=1 THEN ' DESC' ELSE '' END FROM sys.index_columns ic JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE ic.object_id=i.object_id AND ic.index_id=i.index_id AND ic.is_included_column=0 ORDER BY ic.key_ordinal FOR XML PATH('')),1,1,'') chaves,
  (SELECT TOP 1 data_compression_desc FROM sys.partitions p WHERE p.object_id=i.object_id AND p.index_id=i.index_id) compressao
FROM sys.tables t JOIN sys.indexes i ON i.object_id=t.object_id ORDER BY t.name, i.index_id;
SELECT COUNT(*) AS indices_nao_clusterizados FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id WHERE i.type_desc='NONCLUSTERED';
PRINT '== estatisticas existentes';
SELECT t.name tabela, s.name estat, s.auto_created, s.user_created, s.no_recompute, s.is_incremental,
  STUFF((SELECT ','+c.name FROM sys.stats_columns sc JOIN sys.columns c ON c.object_id=sc.object_id AND c.column_id=sc.column_id WHERE sc.object_id=s.object_id AND sc.stats_id=s.stats_id ORDER BY sc.stats_column_id FOR XML PATH('')),1,1,'') colunas,
  sp.last_updated, sp.rows, sp.rows_sampled, sp.modification_counter
FROM sys.tables t JOIN sys.stats s ON s.object_id=t.object_id OUTER APPLY sys.dm_db_stats_properties(s.object_id,s.stats_id) sp ORDER BY t.name, s.stats_id;
PRINT '== Query Store';
SELECT actual_state_desc, desired_state_desc, query_capture_mode_desc, interval_length_minutes, flush_interval_seconds, max_storage_size_mb, wait_stats_capture_mode_desc FROM sys.database_query_store_options;
