import ast
import os
from ezmon.common import get_logger

logger = get_logger(__name__)

# networkx and pyvis are optional dependencies for graph generation
# They are imported lazily in generate_graph() to avoid import errors
# when users don't have them installed

def scan_project(root_dir):
    project_data = {}
    for root, dirs, files in os.walk(root_dir):
        # Exclude hidden folders and common build/env directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', '__pycache__', 'build', 'dist', 'node_modules']]

        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")
            project_data[rel_path] = get_imports(full_path)
    return project_data

def get_imports(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            tree = ast.parse(content)
    except UnicodeDecodeError:
        # e.g. binary file
        return []
    except SyntaxError:
        return []
    except Exception as e:
        logger.warning(f"Skipping {file_path} due to error: {e}")
        return []

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    imports.add(f"{node.module}.{alias.name}")
                imports.add(node.module)
    return list(imports)

def generate_graph(root_dir, output_file="dependency_graph.html"):
    # Import optional dependencies here to avoid import errors when not installed
    try:
        import networkx as nx
        from pyvis.network import Network
    except ImportError as e:
        logger.error(
            f"Graph generation requires 'networkx' and 'pyvis' packages. "
            f"Install them with: pip install networkx pyvis"
        )
        raise ImportError(
            "Graph generation requires optional dependencies. "
            "Install with: pip install networkx pyvis"
        ) from e

    logger.info(f"Scanning project at {root_dir}.")
    data = scan_project(root_dir)

    logger.info("Building graph...")
    G = nx.DiGraph()

    # Map "module names" to "file paths" for internal files
    module_to_file = {}
    for file_path in data.keys():
        G.add_node(file_path, type='internal', title="Internal Source File")

        module_name = file_path.replace(".py", "").replace("/", ".")
        module_to_file[module_name] = file_path

        if file_path.endswith("__init__.py"):
            pkg_name = module_name.replace(".__init__", "")
            module_to_file[pkg_name] = file_path

    for file, imports in data.items():
        for imp in imports:
            target = None

            # 1. Internal file check
            if imp in module_to_file:
                target = module_to_file[imp]
            elif "." in imp:
                parent = imp.rsplit(".", 1)[0]
                if parent in module_to_file:
                    target = module_to_file[parent]

            # 2. Add edge
            if target:
                if target != file:
                    G.add_edge(file, target)
            else:
                # External library
                external_lib = imp.split('.')[0]
                if external_lib not in G:
                    G.add_node(external_lib, type='external', title="External Library")
                G.add_edge(file, external_lib)

    # Clean up isolated nodes
    isolated = [node for node in G.nodes() if G.degree(node) == 0]
    G.remove_nodes_from(isolated)

    # Style nodes
    for node in G.nodes():
        node_type = G.nodes[node].get('type')
        if node_type == 'internal':
            G.nodes[node]['color'] = '#97c2fc'
            G.nodes[node]['size'] = 20
        else:
            G.nodes[node]['color'] = '#ffb7b2'
            G.nodes[node]['shape'] = 'box'

    # Visualization settings
    net = Network(height="750px", width="100%", bgcolor="#222222", font_color="white", select_menu=True, cdn_resources="in_line")
    net.from_nx(G)
    net.show_buttons(filter_=['physics'])

    output_path = os.path.join(root_dir, output_file)
    net.save_graph(output_path)
    logger.info(f"Dependency graph saved to: {output_path}")