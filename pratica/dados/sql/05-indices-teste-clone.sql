-- Executar SOMENTE no clone StackOverflow2010_teste (restaurado do .bak). Nao rodar no banco base.
SET NOCOUNT ON;
USE StackOverflow2010_teste;
SET STATISTICS TIME ON;
PRINT '##### criar IX_Users_CreationDate';  CREATE INDEX IX_Users_CreationDate ON dbo.Users(CreationDate);
PRINT '##### criar IX_Posts_CreationDate';  CREATE INDEX IX_Posts_CreationDate ON dbo.Posts(CreationDate);
PRINT '##### criar IX_Posts_OwnerUserId';   CREATE INDEX IX_Posts_OwnerUserId ON dbo.Posts(OwnerUserId);
PRINT '##### criar IX_Comments_UserId';     CREATE INDEX IX_Comments_UserId ON dbo.Comments(UserId);
PRINT '##### criar IX_Votes_PostId';        CREATE INDEX IX_Votes_PostId ON dbo.Votes(PostId);
SET STATISTICS TIME OFF;
SELECT i.name, SUM(ps.used_page_count)*8/1024 AS mb FROM sys.indexes i JOIN sys.dm_db_partition_stats ps ON ps.object_id=i.object_id AND ps.index_id=i.index_id WHERE i.type=2 GROUP BY i.name;
