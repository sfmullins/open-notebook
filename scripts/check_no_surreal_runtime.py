#!/usr/bin/env python3
"""Enforce PostgreSQL-only shipped runtime; SurrealDB is migration-source only."""
from __future__ import annotations
import re, sys, tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME_DIRS=tuple(ROOT/p for p in ("api","commands","open_notebook","command_queue","deploy","examples","scripts"))
ROOT_FILES=tuple(ROOT/p for p in ("Dockerfile","docker-compose.yml","docker-compose.dev.yml","docker-compose.override.yml.example","Makefile","dev-init.sh","start.sh","supervisord.conf"))
ALLOWED={ROOT/'scripts'/'migrate_surreal_to_postgres.py', ROOT/'scripts'/'check_no_surreal_runtime.py'}
SUFFIXES={'.py','.toml','.yml','.yaml','.sh','.conf','.env',''}
PATTERNS=(
 (re.compile(r'(?m)^\s*(?:from|import)\s+surrealdb\b'),'SurrealDB Python SDK import'),
 (re.compile(r'surrealdb/surrealdb(?::[^\s\"\']+)?',re.I),'SurrealDB server image'),
 (re.compile(r'\brepo_query\b'),'generic SurrealQL compatibility query API'),
 (re.compile(r'\blegacy_query_compat\b'),'legacy query compatibility module'),
 (re.compile(r'\bsurreal_commands\b'),'obsolete Surreal-named command package'),
 (re.compile(r'\bSURREAL_[A-Z0-9_]+\b'),'obsolete SurrealDB runtime configuration'),
)
def files():
    out={p for p in ROOT_FILES if p.exists()}
    for d in RUNTIME_DIRS:
        if d.exists():
            out.update(p for p in d.rglob('*') if p.is_file() and p.suffix.lower() in SUFFIXES)
    return sorted(out)
def dependency_name(req): return re.split(r'[<>=!~;\[\s]',req,maxsplit=1)[0].strip().lower()
def main():
    failures=[]
    for p in files():
        if p in ALLOWED: continue
        try: txt=p.read_text(encoding='utf-8')
        except UnicodeDecodeError: continue
        for pattern,desc in PATTERNS:
            if pattern.search(txt): failures.append(f"{p.relative_to(ROOT)}: {desc}")
    with (ROOT/'pyproject.toml').open('rb') as fh: project=tomllib.load(fh)
    deps=project.get('project',{}).get('dependencies',[])
    for prohibited in ('surrealdb','surreal-commands','surreal_commands'):
        if any(dependency_name(str(x))==prohibited for x in deps): failures.append(f"pyproject.toml: prohibited dependency {prohibited}")
    if (ROOT/'open_notebook/database/legacy_query_compat.py').exists(): failures.append('legacy_query_compat.py must not exist')
    if failures:
        print('PostgreSQL-only runtime boundary violations detected:',file=sys.stderr)
        for f in failures: print(f'  - {f}',file=sys.stderr)
        return 1
    print('PostgreSQL-only runtime boundary clean.')
    return 0
if __name__=='__main__': raise SystemExit(main())
