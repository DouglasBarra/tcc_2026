"""Exportacao CSV e JSON (formato longo: uma linha por repeticao) a partir do SQLite."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .store import Armazem

SQL_MEDICOES = """
SELECT e.experiment_id, e.name AS experimento, e.seed AS semente, r.run_id, r.seq, r.regime, r.block AS bloco,
       v.nome AS variante, r.query_hash, r.query_plan_hash, m.rep, m.phase, m.logical_reads, m.physical_reads,
       m.read_ahead_reads, m.lob_logical_reads, m.scan_count, m.cpu_ms, m.elapsed_ms, m.compile_cpu_ms,
       m.compile_elapsed_ms, m.cpu_us, m.elapsed_us, m.dmv_logical_reads, m.client_ms, m.rows, m.result_hash, m.ts_utc, m.io_raw, m.time_raw
FROM measurement m JOIN run r ON r.run_id = m.run_id JOIN experiment e ON e.experiment_id = r.experiment_id
JOIN variant v ON v.variant_id = r.variant_id
WHERE e.experiment_id = ? ORDER BY r.seq, m.rep"""

TABELAS = {
    "measurements": SQL_MEDICOES,
    "measurement_io": "SELECT mi.* FROM measurement_io mi JOIN run r ON r.run_id=mi.run_id WHERE r.experiment_id=? ORDER BY mi.run_id, mi.rep",
    "qs_samples": "SELECT q.* FROM qs_sample q JOIN run r ON r.run_id=q.run_id WHERE r.experiment_id=? ORDER BY q.run_id",
    "dmv_samples": "SELECT d.* FROM dmv_sample d JOIN run r ON r.run_id=d.run_id WHERE r.experiment_id=? ORDER BY d.run_id",
    "runs": "SELECT r.*, v.nome AS variante FROM run r JOIN variant v ON v.variant_id=r.variant_id WHERE r.experiment_id=? ORDER BY r.seq",
}


def exportar(armazem: Armazem, experiment_id: int, pasta: str | Path, formato: str = "csv") -> list[Path]:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    exp = armazem.consultar("SELECT * FROM experiment WHERE experiment_id=?", (experiment_id,))
    if not exp:
        raise KeyError(f"experimento {experiment_id} inexistente")
    exp = exp[0]
    env = armazem.consultar("SELECT bruto_json FROM environment WHERE env_id=?", (exp["env_id"],))[0]["bruto_json"]
    escritos: list[Path] = []
    (pasta / "environment.json").write_text(json.dumps(json.loads(env), ensure_ascii=False, indent=2), encoding="utf-8")
    (pasta / "manifest.json").write_text(
        json.dumps(
            {"experiment_id": experiment_id, "semente": exp["seed"], "status": exp["status"],
             "manifesto": json.loads(exp["manifest_json"]), "ordem_executada": json.loads(exp["ordem_executada_json"] or "[]")},
            ensure_ascii=False, indent=2),
        encoding="utf-8")
    escritos += [pasta / "environment.json", pasta / "manifest.json"]
    dados = {nome: [dict(r) for r in armazem.consultar(sql, (experiment_id,))] for nome, sql in TABELAS.items()}
    if formato == "csv":  # texto bruto de STATISTICS tem quebras de linha: achata para manter uma linha por repeticao
        for linha in dados["measurements"]:
            for campo in ("io_raw", "time_raw"):
                linha[campo] = (linha[campo] or "").replace("\r", "").replace("\n", " | ").strip(" |")
    if formato == "csv":
        for nome, linhas in dados.items():
            caminho = pasta / f"{nome}.csv"
            with caminho.open("w", newline="", encoding="utf-8") as f:
                if linhas:
                    w = csv.DictWriter(f, fieldnames=list(linhas[0]))
                    w.writeheader()
                    w.writerows(linhas)
            escritos.append(caminho)
    elif formato == "json":
        caminho = pasta / "dados.json"
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        escritos.append(caminho)
    else:
        raise ValueError(f"formato desconhecido: {formato}")
    return escritos
