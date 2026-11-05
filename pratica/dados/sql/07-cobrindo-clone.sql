SET NOCOUNT ON;
USE StackOverflow2010_teste;
CREATE INDEX IX_Posts_OwnerUserId_cov ON dbo.Posts(OwnerUserId) INCLUDE (Score, CreationDate);
SET STATISTICS IO, TIME ON;
PRINT '##### (c2) colunas listadas user 22656 - com indice cobrindo'; SELECT Id, Score, CreationDate FROM dbo.Posts WHERE OwnerUserId = 22656;
