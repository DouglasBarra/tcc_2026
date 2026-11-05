"""Criterio M5: nenhum dado ficticio no codigo da API nem da interface (fora dos testes)."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BACKEND = RAIZ / "backend" / "tcclab_api"
FRONT = RAIZ / "frontend" / "src"


def _fontes(pasta: Path, sufixos: tuple[str, ...]):
    return [p for p in pasta.rglob("*") if p.suffix in sufixos and "__testes__" not in p.parts and "__pycache__" not in p.parts]


def test_backend_nao_gera_numeros_aleatorios_nem_dados_de_exemplo():
    achados = []
    for p in _fontes(BACKEND, (".py",)):
        txt = p.read_text(encoding="utf-8")
        if re.search(r"^\s*(import|from)\s+(random|faker|numpy\.random)\b", txt, re.M) or re.search(r"\b(faker|lorem ipsum|dummy_data|sample_data)\b", txt, re.I):
            achados.append(p.name)
    assert achados == []


def test_frontend_sem_math_random_e_sem_arquivos_de_dados():
    fontes = _fontes(FRONT, (".js", ".jsx", ".css", ".json", ".csv"))
    assert any(p.suffix == ".jsx" for p in fontes)
    assert [p.name for p in fontes if "Math.random" in p.read_text(encoding="utf-8")] == []
    assert [p.name for p in fontes if p.suffix in (".json", ".csv")] == []


def test_o_unico_mock_do_projeto_original_nao_foi_copiado():
    """O mock em src/ (raiz do TCC) usa Math.random e dados fixos; nada dele pode ter vindo para esta pasta."""
    for p in _fontes(FRONT, (".js", ".jsx")):
        txt = p.read_text(encoding="utf-8")
        assert "sqlmesh" not in txt.lower() and "SQLPipelineEditor" not in txt, p.name
        assert not re.search(r"\$\s*\d+\.\d{2}", txt) or p.name.endswith(".test.jsx"), p.name   # sem valores em dolar
