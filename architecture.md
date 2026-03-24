# Architecture

## Overview

**API Usage Monitor** is built as two cooperating layers:

1. **A static-analysis layer** that scans Python repositories, parses files, binds symbols, resolves imports, and identifies API call sites.
2. **A data pipeline layer** that streams discovered API calls through Kafka, aggregates them with Spark, computes deprecation exposure, and loads results into Postgres under Airflow orchestration.

At a high level:

```text
Airflow
  ├── scanner.main
  │     └── Program
  │           ├── SourceFile.parse(Parser)
  │           ├── Binder.bind_source_file(...)
  │           └── ImportResolver.resolve_imports_for_source_file(...)
  │
  ├── spark_jobs/api_usage_aggregator.py
  ├── spark_jobs/deprecation_exposure.py
  ├── db_loader/load_api_usage.py
  └── db_loader/load_exposure_scores.py
```

The scanner is the core of the system. It is intentionally modeled after a simplified version of the architecture used by **Pyright**:
- `Program` is the central coordinator.
- `SourceFile` owns per-file state.
- `Parser` produces a lightweight custom parse tree.
- `Binder` constructs module-level scopes and symbols.
- `ImportResolver` builds cross-file import edges.
- `Program.collect_api_calls()` uses Python's stdlib `ast` to walk call expressions and classify them as local or external.

---

## Top-Level Runtime Architecture

### Control plane

**Airflow** is the orchestrator. It does not sit "after" Postgres in the data path; it controls the entire DAG:

1. run the scanner
2. run Spark aggregation
3. compute deprecation exposure
4. load aggregated usage into Postgres
5. load exposure scores into Postgres

### Data plane

```text
Code Repositories
   ↓
scanner.main
   ↓
Kafka topic (ast.api_calls)
   ↓
Spark Structured Streaming aggregation
   ↓
Parquet outputs
   ├── results/aggregated_usage
   ├── results/deprecated_usage
   └── results/exposure_scores
   ↓
Postgres
```

---

## Repository Structure

```text
airflow/
  dags/
    api_usage_monitor_dag.py

scanner/
  main.py
  analyzer/
    SourceFile.py
    binder.py
    importResolver.py
    parseTreeWalker.py
    program.py
    scope.py
    service.py
    symbol.py
  common/
    config.py
    kafka_producer.py
    ...
  parser/
    parseNodes.py
    parser.py

spark_jobs/
  common.py
  api_usage_aggregator.py
  deprecation_exposure.py

db_loader/
  connection.py
  load_api_usage.py
  load_exposure_scores.py
```

---

## End-to-End Flow

### 1. Airflow launches the scanner

The DAG task `scan_repos` runs:

```bash
python -m scanner.main --settings configs/scanner_settings.json
```

`scanner.main`:
- loads scanner settings
- creates a Kafka producer
- creates a `Parser`
- creates a builtin `Scope`
- creates a `Binder`
- creates a `Program`
- registers tracked `.py` files per repository
- calls `Program.analyze()`
- calls `Program.collect_api_calls(external_only=True)`
- emits one Kafka event per external API call

### 2. Airflow launches Spark aggregation

`api_usage_aggregator.py` reads Kafka events as a streaming DataFrame, parses JSON payloads, adds time buckets, aggregates usage, and writes Parquet.

### 3. Airflow launches deprecation analysis

`deprecation_exposure.py` joins aggregated usage with `deprecated_apis.json`, computes:

```text
exposure_score = usage_count * severity
```

and writes:
- per-API deprecated usage
- per-repo exposure scores

### 4. Airflow launches DB loaders

The loader scripts read Parquet files into pandas and write them into Postgres using SQLAlchemy.

---

# Static Analysis Architecture

## Core design

The scanner does **not** rely on a single AST representation for everything.

It uses two representations:

1. **Custom lightweight parse tree**
   - `ModuleNode`
   - `ImportNode`
   - `ImportAlias`
   - `CallNode` (defined, but currently not populated by the active parser)

2. **Python stdlib `ast`**
   - used by the binder to discover top-level defs
   - used by the import resolver to inspect imports
   - used by `Program.collect_api_calls()` to find `ast.Call` nodes

That split is important:

- the custom parse tree is currently used mainly to record imports
- the stdlib AST is used for richer semantic work

---

## Main classes and responsibilities

## `scanner.main`

This is the current production entrypoint for analysis.

### Responsibilities
- load logging configuration
- load scanner settings from JSON
- initialize Kafka producer
- initialize parser and binder
- iterate configured repos
- construct `Program` instances
- register tracked files
- run analysis
- collect external API calls
- emit events to Kafka

### Interaction graph

```text
scanner.main
  ├── load_scanner_settings()
  ├── ApiCallProducer(settings.kafka)
  ├── Parser()
  ├── Scope(scope_type=BUILTIN)
  ├── Binder(BinderConfig(...))
  └── Program(config, parser, binder)
         ├── set_tracked_files(...)
         ├── analyze()
         └── collect_api_calls(external_only=True)
```

---

## `SourceFile`

`SourceFile` is the unit of analysis for one Python file.

### Fields

- `path: Path`
  - absolute filesystem path
- `repo: str`
  - logical repository name
- `module: Optional[ModuleNode]`
  - root custom parse tree for this file
- `diagnostics: List[Diagnostic]`
  - parse-time diagnostics
- `_contents_cache: Optional[str]`
  - cached file contents
- `scope: Optional[Scope]`
  - module scope after binding
- `import_edges: List[ImportResolution]`
  - cross-file import graph edges

### Methods

#### `read_contents()`
Reads and caches the text of the source file.

#### `invalidate_contents()`
Clears cache, parse results, and diagnostics.

#### `parse(parser: Parser)`
Calls the parser and stores:
- `module` on success
- `Diagnostic` entries on failure

### Diagnostic structure

```text
Diagnostic
  - message: str
  - file: str
  - line: int
  - column: int
```

---

## `Parser`

The active parser is intentionally minimal.

### Current behavior
- parses Python source with `ast.parse`
- creates a `ModuleNode`
- visits only import statements
- records imports into `ModuleNode.imports`
- does **not** currently populate `ModuleNode.calls`
- does **not** emit Kafka events directly

### Internal walker: `_ModuleOnlyVisitor`

This `ast.NodeVisitor` implements:

#### `visit_Import`
For code like:

```python
import pandas as pd
import requests
```

it creates one `ImportNode` with one or more `ImportAlias` items.

#### `visit_ImportFrom`
For code like:

```python
from pandas import read_csv as rc
from os.path import join
```

it creates a `ImportNode` with `is_from_import=True` and alias entries.

---

## Custom parse tree data structures

## `ParseNodeType`

Current symbolic node types:
- `MODULE`
- `CALL`
- `IMPORT`
- `IMPORT_FROM`

## `ParseNodeBase`

Base fields shared by all custom nodes:

- `node_type: str`
- `start: int`
- `end: int`
- `parent: Optional[ParseNodeBase]`

### Notes
- `start` / `end` are lightweight source positions
- `parent` exists structurally, but the active parser does not currently wire parent pointers deeply

## `ModuleNode`

Represents the root node for one file.

### Fields
- `file_path: str`
- `repo: str`
- `calls: List[CallNode]`
- `imports: List[ImportNode]`

### What is currently populated?
- `imports`: yes
- `calls`: no, not by the active parser

## `ImportAlias`

Represents one imported name.

### Fields
- `module: str`
- `name: Optional[str]`
- `alias: Optional[str]`

### Examples

```text
import pandas as pd
  module='pandas', name=None, alias='pd'

from pandas import read_csv as rc
  module='pandas', name='read_csv', alias='rc'
```

## `ImportNode`

Represents an `import` or `from ... import ...` statement.

### Fields
- `module: str`
- `aliases: List[ImportAlias]`
- `is_from_import: bool`

## `CallNode`

Defined for future/custom call capture.

### Fields
- `fq_name: str`
- `name: str`
- `base: Optional[str]`
- `positional_count: int`
- `keyword_args: List[str]`

### Current status
This node exists in the data model, but the active parser no longer emits it. Call discovery is currently done by `Program.collect_api_calls()` using the stdlib AST.

---

## `Program`

`Program` is the central analysis coordinator.

### Responsibilities
- track all `SourceFile` objects for a repo
- parse files
- bind scopes
- resolve imports
- build import graph
- resolve symbols
- collect API calls

### Important fields

- `_config: ProgramConfig`
- `_parser: Parser`
- `_binder: Optional[Binder]`
- `_import_resolver: ImportResolver`
- `_files: Dict[Path, SourceFile]`
- `_import_graph: Dict[Path, List[ImportResolution]]`

## `ProgramConfig`

Fields:
- `project_root: Path`
- `target_libraries: List[str]`

### Important note
`target_libraries` is retained for compatibility, but the current implementation classifies external APIs by import locality, not by library allow-list matching.

---

## `Program` lifecycle

### `set_tracked_files(files, repo_name)`

Creates one `SourceFile` per tracked path and stores it in `_files`.

### `analyze()`

For each tracked file:

```text
_parse_bind_resolve(sf)
  ├── sf.parse(parser)
  ├── binder.bind_source_file(sf)      # if binder exists and module exists
  └── import_resolver.resolve_imports_for_source_file(sf)
```

### `_parse_bind_resolve(sf)`

This is the main per-file semantic pipeline:
1. parse the file into a `ModuleNode`
2. bind module-level names into a `Scope`
3. resolve imports into `ImportResolution` edges
4. attach import edges to `SourceFile`
5. update `_import_graph`

### `build_dependency_graph()`

Recomputes import edges for all files.

### `get_dependents_of(path)`

Returns all tracked files that import the given file.

---

## Symbol binding

## `Binder`

The binder is responsible for creating module-level scopes and registering symbols.

### `BinderConfig`
- `builtin_scope: Scope`

### Binding strategy

For each `SourceFile`, the binder:
1. creates a module scope whose parent is the builtin scope
2. binds custom-parser imports from `ModuleNode.imports`
3. parses the file again with stdlib `ast`
4. binds top-level defs:
   - functions
   - classes
   - simple assignments

### `bind_source_file(source_file)`

Creates:

```text
module_scope = Scope(scope_type=MODULE, parent=builtin_scope)
```

Then attaches it to `source_file.scope`.

---

## `Scope`

A scope owns a symbol table and child scopes.

### Fields
- `scope_type: ScopeType`
- `parent: Optional[Scope]`
- `symbols: SymbolTable`
- `children: List[Scope]`

### `ScopeType`

Current values:
- `BUILTIN`
- `MODULE`
- `CLASS`
- `FUNCTION`
- `COMPREHENSION`

### Methods

#### `add_symbol(name, kind="variable", target=None, declaration=None)`
Creates or updates a symbol in the current scope.

#### `get_symbol(name)`
Looks up only in the current scope.

#### `lookup(name)`
Walks current scope → parent → parent ... until found.

#### `iter_symbols()`
Iterates current-scope symbols.

#### `to_dict()`
Returns a JSON-serializable scope snapshot.

---

## `Symbol`

Represents a name bound in a symbol table.

### Fields
- `name: str`
- `flags: SymbolFlags`
- `target: Optional[str]`
- `declarations: List[...]`
- `_id: int`

### Meaning of `target`

For imports, `target` stores the fully-qualified target:
- `"pandas"`
- `"pandas.read_csv"`
- `"os.path.join"`

For non-imports, `target` is usually `None`.

### Declarations

The current binder stores lightweight declaration strings such as:
- `"<import>"`
- `"import pandas as pd  (/repo/file.py @ offset 12)"`
- `"def build(... )  (/repo/file.py:14:0)"`
- `"class MyClass  (/repo/file.py:21:0)"`
- `"VALUE = <value>  (/repo/file.py:33:0)"`

### `SymbolFlags`

A Pyright-inspired bitflag set exists, including:
- `INITIALLY_UNBOUND`
- `EXTERNALLY_HIDDEN`
- `CLASS_MEMBER`
- `INSTANCE_MEMBER`
- `PRIVATE_MEMBER`
- `CLASS_VAR`
- `IN_DUNDER_ALL`
- `FINAL_VAR_IN_CLASS_BODY`
- and others

### Important note
The flags are defined, but the current binder does not set most of them yet.

---

## What is registered in the current binder?

The binder currently registers only **module-level** symbols.

### Imported symbols

For `import pkg as p`
- local name registered: `p`
- `target = "pkg"`

For `import pkg.sub`
- local name registered: `"pkg.sub"` or alias if provided
- `target = "pkg.sub"`

For `from pkg import x as y`
- local name registered: `y`
- `target = "pkg.x"`

For `from pkg import x`
- local name registered: `x`
- `target = "pkg.x"`

For `from pkg import *`
- local fallback name registered: `"pkg"`
- `target = "pkg"`

### Top-level functions

For:

```python
def build_index(...):
    ...
```

registered symbol:
- `name = "build_index"`
- `kind = "function"`
- declaration text includes line/column

### Top-level classes

For:

```python
class ImportResolver:
    ...
```

registered symbol:
- `name = "ImportResolver"`
- `kind = "class"`

### Top-level assignments

For:

```python
MAX_ROWS = 100
settings: dict = {}
```

registered symbol(s):
- `MAX_ROWS`
- `settings`

### What is *not* yet registered?
- local variables inside functions
- nested function scopes
- class body members
- instance members
- inferred types
- method symbols per class scope
- attribute graphs

The current scope model supports future expansion, but the binder stays intentionally shallow.

---

## How imports become cross-file edges

## `ImportResolver`

The import resolver uses stdlib `ast` rather than the custom parse tree.

### Output structure: `ImportResolution`

Fields:
- `module: str`
- `file: Optional[Path]`
- `is_local: bool`
- `imported_names: List[str]`

### Resolution process

For every `ast.Import` and `ast.ImportFrom`:
1. compute the effective module name
2. map module name to a file under `project_root`
3. mark whether that resolved file is local
4. store imported names for `from ... import ...`

### Example

```python
from utils.parsers import normalize, parse
```

may produce:

```text
ImportResolution(
  module="utils.parsers",
  file=/repo/utils/parsers.py,
  is_local=True,
  imported_names=["normalize", "parse"]
)
```

### Relative import handling

`_compute_base_module_for_from(...)` resolves:
- `from . import x`
- `from .utils import y`
- `from ..pkg import z`

It computes the current file's module name relative to `project_root`, then walks up package levels according to `level`.

### Path mapping

`_resolve_module_to_path("pkg.sub.mod")` tries:

1. `project_root/pkg/sub/mod.py`
2. `project_root/pkg/sub/mod/__init__.py`

If neither exists, the import is treated as unresolved / non-local.

---

## How names resolve across files

## `Program.resolve_symbol(file, name)`

This method resolves a single identifier in a file.

### Cases

#### 1. Local symbol in the module scope
Example:
```python
def helper(): ...
helper()
```

Returns a `ResolvedSymbol` with:
- `is_import=False`
- `defining_file=current file`
- `import_module=None`

#### 2. Imported symbol whose module is local
Example:
```python
from project.utils import helper
helper()
```

Returns:
- `is_import=True`
- `import_module="project.utils.helper"`
- `import_is_local=True`
- `defining_file=/repo/project/utils.py` if the import edge resolves there

#### 3. Imported symbol whose module is external
Example:
```python
import pandas as pd
pd.read_csv(...)
```

Returns:
- `is_import=True`
- `import_module="pandas"`
- `import_is_local=False`
- `defining_file=None`

### `ResolvedSymbol` fields

- `name: str`
- `symbol: Symbol`
- `defining_file: Optional[Path]`
- `is_import: bool`
- `import_module: Optional[str]`
- `import_is_local: Optional[bool]`

---

## Qualified names

## `Program.resolve_qualified_symbol(file, base_name, attr_name)`

Resolves expressions like:

```python
pd.read_csv
utils.normalize
```

### Output: `QualifiedResolvedSymbol`

Fields:
- `base: ResolvedSymbol`
- `attr_name: str`
- `attr_symbol: Optional[Symbol]`
- `attr_defining_file: Optional[Path]`

### Current behavior
If the base import resolves to a **local module file**, `Program` will look inside that target file's module scope for `attr_name`.

This is a lightweight cross-file symbol lookup. It does not yet do full attribute/type inference.

---

## How AST call walking works

## `Program.collect_api_calls(external_only=True)`

This is where API calls are discovered today.

### Per-file algorithm

For each `SourceFile`:
1. read source text
2. parse with stdlib `ast.parse`
3. walk every node with `ast.walk(tree)`
4. select `ast.Call` nodes
5. extract a call qualifier from `node.func`
6. resolve the base symbol using module scope + import graph
7. classify the call as local or external
8. compute a normalized symbol string
9. compute a lightweight call signature shape
10. emit an `ApiCall`

### Supported function expression shapes

#### Direct name call
```python
foo(...)
```

Produces:
- `base_name = "foo"`
- `attr_chain = []`

#### Attribute chain call
```python
pd.read_csv(...)
client.session.get(...)
```

Produces:
- `base_name = "pd"` / `"client"`
- `attr_chain = ["read_csv"]` / `["session", "get"]`

### Unsupported / currently skipped shapes
- lambda calls
- dynamic expressions like `(factory())(...)`
- subscripts returning callables
- more complex runtime dispatch cases

---

## `ApiCall`

This is the normalized call-site record returned by `Program.collect_api_calls()`.

### Fields

- `repo: str`
- `file: Path`
- `symbol_called: str`
- `library: str`
- `signature_shape: Dict[str, object]`
- `is_external: bool`
- `defining_file: Optional[Path]`
- `import_module: Optional[str]`

### `signature_shape`

Computed from `ast.Call`:
- `positional_count`
- `keyword_args`
- `has_varargs`
- `has_varkw`

Example:

```python
pd.read_csv(path, sep=",", header=0, **opts)
```

may produce:

```json
{
  "positional_count": 1,
  "keyword_args": ["header", "sep"],
  "has_varargs": false,
  "has_varkw": true
}
```

### `symbol_called` construction

#### Imported module alias
```python
import pandas as pd
pd.read_csv(...)
```

becomes:
- `import_module = "pandas"`
- `attr_chain = ["read_csv"]`
- `symbol_called = "pandas.read_csv"`

#### Imported symbol alias
```python
from pandas import read_csv as rc
rc(...)
```

becomes:
- `import_module = "pandas.read_csv"`
- `attr_chain = []`
- `symbol_called = "pandas.read_csv"`

#### Local function
```python
helper(...)
```

becomes:
- `symbol_called = "helper"`

---

## External vs local classification

The current rule is:

```text
external = resolved.is_import and not resolved.import_is_local
```

So a call is considered **external** when:
- its base name came from an import
- and that import does not resolve under the current `project_root`

This means both:
- third-party packages
- stdlib modules
- unresolved non-local imports

are currently treated as external.

This is a deliberate design change from a simple `target_libraries` allow-list approach.

---

# Pipeline Data Structures

## Scanner settings

## `ScannerSettings`

Fields:
- `repos: List[str]`
- `include_extensions: List[str]`
- `exclude_dirs: List[str]`
- `target_libraries: List[str]`
- `kafka: KafkaSettings`

## `KafkaSettings`

Fields:
- `enabled: bool`
- `bootstrap_servers: str`
- `topic: str`
- `client_id: str`

### Config loading behavior

`load_json(name)` looks in several locations:
1. `scanner/configs/<name>`
2. `<PROJECT_ROOT>/configs/<name>`
3. `<PROJECT_ROOT>/<name>`
4. `SCANNER_CONFIG_DIR/<name>`

This makes the scanner usable from different working directories and deployment layouts.

---

## Kafka producer

## `ApiCallProducer`

Thin wrapper around `KafkaProducer`.

### Responsibilities
- initialize producer if Kafka is enabled
- JSON-serialize event payloads
- send events to configured topic
- flush pending messages

### Behavior when Kafka is disabled
Events are logged instead of sent.

---

## Scanner-emitted event shape

`scanner.main` emits one JSON event per discovered external API call.

Current event fields are:

- `repo`
- `file`
- `symbol_called`
- `library`
- `signature_shape`
- `is_external`
- `defining_file`
- `import_module`
- `commit_hash`
- `timestamp`

### Important implementation note

The Spark-side schema in `spark_jobs/common.py` currently expects fields named:

- `repo_name`
- `file_path`
- `call_full_name`
- `event_time`

while `scanner.main` currently emits:

- `repo`
- `file`
- `symbol_called`
- `timestamp`

That means the event contract likely needs alignment unless there is an adapter layer elsewhere. The architecture is clear, but the payload schema should be normalized before relying on strict end-to-end compatibility.

---

# Spark Layer

## `spark_jobs/common.py`

Shared Spark helpers include:
- logger setup
- `build_spark(app_name)`
- `ensure_dir(path)`
- `add_month_bucket(df, time_col=...)`
- `parse_kafka_value(df)`
- Kafka JSON schema for event parsing
- deprecated API schema for JSON metadata

## `api_usage_aggregator.py`

### Purpose
Read API-call events from Kafka and aggregate usage over time.

### Flow

```text
Kafka stream
  → parse value JSON
  → cast event timestamp
  → add month bucket
  → add watermark
  → group by window + library + symbol
  → write Parquet
```

### Key functions
- `build_source_stream(...)`
- `build_aggregated_usage_stream(...)`
- `parse_args()`
- `main()`

### Notes
- uses Structured Streaming
- uses watermarking
- writes parquet with checkpointing
- currently groups by day window, library, symbol

---

## `deprecation_exposure.py`

### Purpose
Join aggregated API usage with deprecated API metadata and compute exposure scores.

### Inputs
- aggregated usage Parquet
- `deprecated_apis.json`

### Outputs
- `results/deprecated_usage`
- `results/exposure_scores`

### Metrics
- `exposure_score`
- `total_deprecated_calls`
- `unique_deprecated_apis`

---

# Database Loaders

## `load_api_usage.py`

### Purpose
Read aggregated Parquet and write into table `api_usage`.

### Expected columns
- `repo`
- `symbol_called`
- `time_window`
- `usage_count`

## `load_exposure_scores.py`

### Purpose
Read exposure Parquet and write into table `repo_exposure`.

### Expected columns
- `repo`
- `time_window`
- `exposure_score`
- `total_deprecated_calls`
- `unique_deprecated_apis`

### Loading model
Both loaders:
- gather all `*.parquet` files recursively
- read into pandas DataFrames
- concatenate parts
- validate required columns
- write with `DataFrame.to_sql(...)`

---

# Airflow DAG Architecture

## `api_usage_monitor_dag.py`

The DAG defines the orchestration graph:

```text
scan_repos
  ↓
spark_api_usage
  ↓
spark_deprecation_exposure
  ├── load_api_usage_to_db
  └── load_exposure_scores_to_db
```

### Task roles

#### `scan_repos`
Runs the scanner entrypoint.

#### `spark_api_usage`
Runs `spark-submit` on `spark_jobs/api_usage_aggregator.py`.

#### `spark_deprecation_exposure`
Runs `spark-submit` on `spark_jobs/deprecation_exposure.py`.

#### `load_api_usage_to_db`
Loads aggregated usage into Postgres.

#### `load_exposure_scores_to_db`
Loads repo exposure scores into Postgres.

### Airflow datasets
The DAG uses an `aggregated_dataset` dataset to annotate the aggregated usage output path for upstream/downstream semantics.

---

# Detailed Interaction Schemas

## Analysis-phase object graph

```text
Program
  ├── ProgramConfig
  ├── Parser
  ├── Binder
  ├── ImportResolver
  ├── _files: Path -> SourceFile
  └── _import_graph: Path -> [ImportResolution]

SourceFile
  ├── module: ModuleNode
  ├── scope: Scope
  ├── diagnostics: [Diagnostic]
  └── import_edges: [ImportResolution]

Scope
  ├── symbols: name -> Symbol
  ├── parent: Scope | None
  └── children: [Scope]

Symbol
  ├── name
  ├── flags
  ├── target
  └── declarations
```

## Parse-phase schema

```text
ModuleNode
  ├── imports: [ImportNode]
  └── calls: [CallNode]   # currently unused by active parser

ImportNode
  ├── module
  ├── aliases: [ImportAlias]
  └── is_from_import

ImportAlias
  ├── module
  ├── name
  └── alias
```

## Import graph schema

```text
Path -> [ImportResolution]

ImportResolution
  ├── module: str
  ├── file: Path | None
  ├── is_local: bool
  └── imported_names: [str]
```

## Call analysis schema

```text
ApiCall
  ├── repo
  ├── file
  ├── symbol_called
  ├── library
  ├── signature_shape
  ├── is_external
  ├── defining_file
  └── import_module
```

---

# Parent Classes and Inheritance Notes

There is very little inheritance in the current codebase.

## Present inheritance / base usage

### `ParseNodeBase`
Base dataclass for:
- `CallNode`
- `ImportNode`
- `ModuleNode`

### `ast.NodeVisitor`
`_ModuleOnlyVisitor` inherits from Python stdlib `ast.NodeVisitor`.

### Protocol-based interface
`DeclarationLike` is a `Protocol` used to represent declaration-shaped objects.

### Dataclass composition is favored over deep inheritance
Most of the architecture uses:
- dataclasses
- composition
- explicit object graphs

rather than large inheritance trees.

---

# What the current design does well

- clean separation of file state, binding, import resolution, and call extraction
- lightweight but extensible symbol model
- import-locality-based external API detection
- modular pipeline from analysis through storage
- Airflow-controlled orchestration
- Pyright-inspired structure without Pyright-level complexity

---

# Current limitations and extension points

## Current limitations
- binder is module-level only
- no nested scopes are built for functions/classes yet
- no type inference
- no class member registration
- no full attribute resolution
- custom parse tree does not yet carry all semantic information
- event schema between scanner and Spark appears to need normalization

## Clear extension points
- populate `ModuleNode.calls`
- add class/function scopes in `Binder`
- register class members and instance members
- expand `SymbolFlags` usage
- add richer declaration objects instead of strings
- build a stronger call graph
- normalize the Kafka event contract
- add repository commit metadata
- separate stdlib from third-party "external" imports

---

# Summary

The architecture is centered on `Program`, which coordinates parsing, binding, import resolution, and call discovery across `SourceFile` objects. The scanner creates a lightweight semantic model of a repository, extracts normalized API call records, and hands them to the data pipeline through Kafka. Spark and Airflow then turn those events into usage aggregates and deprecation exposure metrics.

In short:

- **Parser** records import structure.
- **Binder** registers module-level names.
- **ImportResolver** maps imports to local or external modules.
- **Program** uses those pieces to resolve names and classify API calls.
- **Airflow** orchestrates the full end-to-end system.
- **Spark + Postgres** persist and summarize the analysis results.
