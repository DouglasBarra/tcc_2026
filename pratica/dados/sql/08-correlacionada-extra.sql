SET NOCOUNT ON;
USE StackOverflow2010_teste;
SET STATISTICS IO, TIME ON;
PRINT '##### (b3) 3 subconsultas correlacionadas em Posts';
SELECT u.Id, u.DisplayName,
  (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Qtd,
  (SELECT MAX(p.Score) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS MaxScore,
  (SELECT SUM(p.ViewCount) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Views
FROM dbo.Users u WHERE u.Reputation >= 20000;
PRINT '##### (b4) equivalente com LEFT JOIN + GROUP BY';
SELECT u.Id, u.DisplayName, COUNT(p.Id) AS Qtd, MAX(p.Score) AS MaxScore, SUM(p.ViewCount) AS Views
FROM dbo.Users u LEFT JOIN dbo.Posts p ON p.OwnerUserId = u.Id WHERE u.Reputation >= 20000 GROUP BY u.Id, u.DisplayName;
PRINT '##### (b5) correlacionada no WHERE';
SELECT u.Id, u.DisplayName FROM dbo.Users u WHERE u.Reputation >= 20000 AND (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 100;
PRINT '##### (b6) equivalente com JOIN + HAVING';
SELECT u.Id, u.DisplayName FROM dbo.Users u JOIN dbo.Posts p ON p.OwnerUserId = u.Id WHERE u.Reputation >= 20000 GROUP BY u.Id, u.DisplayName HAVING COUNT(*) > 100;
