import pytest

from tcclab.config import Config
from tcclab.driver import conectar
from tcclab.medir import preparar_sessao

DDL_TABELA = """
IF OBJECT_ID('dbo.m0_sanidade') IS NOT NULL DROP TABLE dbo.m0_sanidade;
CREATE TABLE dbo.m0_sanidade(id INT NOT NULL PRIMARY KEY, cliente INT NOT NULL, valor DECIMAL(10,2) NOT NULL,
                             pad CHAR(200) NOT NULL DEFAULT 'x');
INSERT dbo.m0_sanidade(id, cliente, valor)
SELECT TOP (200000) n, n % 5000, 10
FROM (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n FROM sys.all_objects a CROSS JOIN sys.all_objects b) t;
"""

CONSULTA = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE cliente = 42"
DDL_INDICE = "CREATE INDEX ix_m0_cliente ON dbo.m0_sanidade(cliente) INCLUDE (valor)"
DDL_REVERTER = "DROP INDEX ix_m0_cliente ON dbo.m0_sanidade"


@pytest.fixture(scope="session")
def cfg():
    return Config.carregar()


@pytest.fixture(scope="session")
def tabela(cfg):
    """Cria dbo.m0_sanidade (200.000 linhas, CHAR(200), determinista) em tcc_lab e remove ao final."""
    try:
        con = conectar(cfg, "tcc_lab")
    except Exception as e:  # servidor fora do ar
        pytest.skip(f"SQL Server de pratica/infra indisponivel: {e}")
    con.executar_lote(DDL_TABELA)
    con.executar_lote(
        "IF INDEXPROPERTY(OBJECT_ID('dbo.m0_sanidade'),'ix_m0_cliente','IndexId') IS NOT NULL DROP INDEX ix_m0_cliente ON dbo.m0_sanidade"
    )
    yield "dbo.m0_sanidade"
    con.executar_lote("IF OBJECT_ID('dbo.m0_sanidade') IS NOT NULL DROP TABLE dbo.m0_sanidade")
    con.fechar()


@pytest.fixture()
def con(cfg, tabela):
    c = conectar(cfg, "tcc_lab")
    preparar_sessao(c)
    yield c
    c.fechar()
