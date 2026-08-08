#!/usr/bin/env python3
"""Sanitiza a saída real de `claude agents --json --all` para versionar como fixture.

Regras (SPEC §2.1):
- cwd reais -> caminhos genéricos
- nomes de cliente/projeto -> genéricos
- preservar a FORMA: mesmos campos, tipos, cardinalidade, e a relação
  nome-default <-> basename(cwd) (heurística G1)
- sessionId re-gerado, preservando o invariante observado: id (short) = 8
  primeiros hex do sessionId
"""
import json
import sys
import uuid

# mapeamento cwd real -> cwd genérico (mesma cardinalidade: dois cwds iguais
# continuam iguais)
CWD_MAP = {
    "/Users/marcelo/projects/inpowered/s3-data": "/home/user/projects/data-pipeline",
    "/Users/marcelo": "/home/user",
    "/Users/marcelo/projects/congado/reconhecimento-focinho": "/home/user/projects/project-a",
    "/Users/marcelo/projects/inpowered/benji-dp": "/home/user/projects/project-b",
    "/Users/marcelo/projects/diastech/emerson": "/home/user/projects/project-c",
    "/Users/marcelo/projects/inpowered/rtb-index-exchange": "/home/user/projects/project-d",
    "/Users/marcelo/projects/diastech/tarmac/.claude/worktrees/task0-findings":
        "/home/user/projects/tarmac/.claude/worktrees/task0-findings",
}

# nomes: defaults preservam o padrão ^<basename(cwd)>-[a-z0-9]{2}$
NAME_MAP = {
    "Fix S3 data path and date format migration script":
        "Fix data path and date format migration script",
    "marcelo-e9": "user-e9",
    "focinho-report": "project-a-report",
    "wsi-nexxen-clickhouse-ingestion": "warehouse-ingestion",
    "camera": "camera",
    "rtb-index-exchange-62": "project-d-62",
    "benji-dp-a4": "project-b-a4",
    "tarmac-t0": "tarmac-t0",
    "tarmac-a3-done-test": "tarmac-a3-done-test",
}


def fresh_ids(entry, rng_seed):
    new = uuid.UUID(int=rng_seed * 0x0123456789ABCDEF0123456789ABCDEF % (1 << 128))
    sid = str(new)
    if "sessionId" in entry:
        entry["sessionId"] = sid
    if "id" in entry:
        entry["id"] = sid[:8]  # invariante observado: short id = prefixo do UUID
    return entry


def main(src, dst):
    with open(src) as f:
        data = json.load(f)
    out = []
    for i, e in enumerate(sorted(data, key=lambda x: x.get("startedAt", 0))):
        e = dict(e)
        e["cwd"] = CWD_MAP[e["cwd"]]
        if "name" in e:
            e["name"] = NAME_MAP[e["name"]]
        out.append(fresh_ids(e, i + 7))
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"wrote {dst}: {len(out)} sessions")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
