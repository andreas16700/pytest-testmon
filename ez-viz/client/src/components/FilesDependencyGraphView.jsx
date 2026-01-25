import React, {
  useMemo,
  useCallback,
  useEffect,
  useState,
  useRef
} from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState
} from "reactflow";
import "reactflow/dist/style.css";

const DEP_FIELD = "dependencies";
const EXT_DEP_FIELD = "external_dependencies";

// Colors matching the old pyvis style
const COLORS = {
  background: '#222222',
  internal: '#97c2fc',    // Blue for internal files
  external: '#ffb7b2',    // Pink/salmon for external libs
  edge: '#848484',
  edgeHighlight: '#ffffff',
  text: '#ffffff',
  textDark: '#222222',
};

function isExternalPackage(name) {
  // External packages don't have path separators and don't end in .py
  return !name.includes('/') && !name.includes('\\') && !name.endsWith('.py');
}

/**
 * Force-directed physics simulation matching pyvis behavior
 */
function applyPhysicsLayout(nodes, edges, iterations = 150) {
  const nodeMap = new Map(nodes.map(n => [n.id, {
    ...n,
    x: (Math.random() - 0.5) * 800,
    y: (Math.random() - 0.5) * 600,
    vx: 0,
    vy: 0
  }]));

  const repulsion = 80000;
  const attraction = 0.015;
  const damping = 0.85;
  const centerGravity = 0.01;

  for (let iter = 0; iter < iterations; iter++) {
    const nodeList = Array.from(nodeMap.values());

    // Repulsion between all nodes
    for (let i = 0; i < nodeList.length; i++) {
      for (let j = i + 1; j < nodeList.length; j++) {
        const n1 = nodeList[i];
        const n2 = nodeList[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = repulsion / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        n1.vx -= fx;
        n1.vy -= fy;
        n2.vx += fx;
        n2.vy += fy;
      }
    }

    // Attraction along edges
    edges.forEach(e => {
      const source = nodeMap.get(e.source);
      const target = nodeMap.get(e.target);
      if (!source || !target) return;

      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = dist * attraction;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      source.vx += fx;
      source.vy += fy;
      target.vx -= fx;
      target.vy -= fy;
    });

    // Center gravity to prevent drift
    nodeList.forEach(n => {
      n.vx -= n.x * centerGravity;
      n.vy -= n.y * centerGravity;
    });

    // Update positions with damping
    nodeList.forEach(n => {
      n.x += n.vx;
      n.y += n.vy;
      n.vx *= damping;
      n.vy *= damping;
    });
  }

  return nodes.map(n => {
    const simNode = nodeMap.get(n.id);
    const isExternal = isExternalPackage(n.id);

    return {
      ...n,
      position: { x: simNode.x, y: simNode.y },
      style: {
        fontSize: '11px',
        padding: '8px 12px',
        background: isExternal ? COLORS.external : COLORS.internal,
        color: COLORS.textDark,
        border: 'none',
        borderRadius: isExternal ? '4px' : '20px', // Box for external, rounded for internal
        fontWeight: 500,
        boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
        minWidth: isExternal ? '80px' : '60px',
        textAlign: 'center',
      }
    };
  });
}

/**
 * Build graph from API data, including external dependencies
 */
function buildGraphFromRuns(runs) {
  const runIdByFile = new Map();
  const allNodes = new Set();
  const rawEdges = [];
  const seenEdges = new Set();

  for (const run of runs || []) {
    for (const f of run.files || []) {
      const file = f.filename;
      if (!file) continue;

      allNodes.add(file);

      if (!runIdByFile.has(file)) {
        runIdByFile.set(file, run.run_id);
      }

      // Internal dependencies
      const deps = Array.isArray(f[DEP_FIELD]) ? f[DEP_FIELD] : [];
      for (const dep of deps) {
        if (!dep || dep === file) continue;
        allNodes.add(dep);

        const edgeKey = `${file}|${dep}`;
        const reverseKey = `${dep}|${file}`;
        if (!seenEdges.has(edgeKey) && !seenEdges.has(reverseKey)) {
          seenEdges.add(edgeKey);
          rawEdges.push({ source: file, target: dep });
        }
      }

      // External dependencies
      const extDeps = Array.isArray(f[EXT_DEP_FIELD]) ? f[EXT_DEP_FIELD] : [];
      for (const extDep of extDeps) {
        if (!extDep) continue;
        allNodes.add(extDep);

        const edgeKey = `${file}|${extDep}`;
        if (!seenEdges.has(edgeKey)) {
          seenEdges.add(edgeKey);
          rawEdges.push({ source: file, target: extDep });
        }
      }
    }
  }

  // Remove isolated external nodes (no edges)
  const connectedNodes = new Set();
  rawEdges.forEach(e => {
    connectedNodes.add(e.source);
    connectedNodes.add(e.target);
  });

  // Create nodes
  const nodes = Array.from(allNodes)
    .filter(name => connectedNodes.has(name))
    .map((name) => {
      const isExternal = isExternalPackage(name);
      return {
        id: name,
        type: 'default',
        data: {
          label: isExternal ? name : (name.split('/').pop() || name),
          fullPath: name,
          isExternal
        },
        position: { x: 0, y: 0 }
      };
    });

  // Create edges
  const edges = rawEdges.map(e => ({
    id: `${e.source}|${e.target}`,
    source: e.source,
    target: e.target,
    type: 'default',
    style: {
      stroke: COLORS.edge,
      strokeWidth: 1.5,
    },
    animated: false,
  }));

  // Apply physics layout
  const layoutedNodes = applyPhysicsLayout(nodes, edges);

  return { nodes: layoutedNodes, edges, runIdByFile };
}

export default function FilesDependencyGraphView({
  repoId,
  jobId,
  runId,
  onOpenFile,
  height = 650
}) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [physicsEnabled, setPhysicsEnabled] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const runIdByFileRef = useRef(new Map());
  const intervalRef = useRef(null);

  useEffect(() => {
    if (!repoId || !jobId || runId == null) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    const url = `/api/data/${repoId}/${jobId}/${runId}/fileDependencies`;

    fetch(url, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(
            `HTTP ${res.status} ${res.statusText} - ${text.slice(0, 180)}`
          );
        }
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setRuns([
          {
            run_id: data.run_id,
            files: data.files || []
          }
        ]);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error("Failed to load file dependencies", err);
        setError(err.message || "Failed to load file dependencies");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [repoId, jobId, runId]);

  // Build graph when runs change
  useEffect(() => {
    if (runs.length === 0) return;

    const { nodes: newNodes, edges: newEdges, runIdByFile } = buildGraphFromRuns(runs);
    setNodes(newNodes);
    setEdges(newEdges);
    runIdByFileRef.current = runIdByFile;
  }, [runs, setNodes, setEdges]);

  // Physics simulation loop
  useEffect(() => {
    if (!physicsEnabled || nodes.length === 0) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    const simulate = () => {
      setNodes(currentNodes => {
        const nodeMap = new Map(currentNodes.map(n => [n.id, {
          ...n,
          x: n.position.x,
          y: n.position.y,
          vx: n.vx || 0,
          vy: n.vy || 0
        }]));

        const repulsion = 50000;
        const attraction = 0.008;
        const damping = 0.9;
        const centerGravity = 0.005;

        const nodeList = Array.from(nodeMap.values());

        // Repulsion
        for (let i = 0; i < nodeList.length; i++) {
          for (let j = i + 1; j < nodeList.length; j++) {
            const n1 = nodeList[i];
            const n2 = nodeList[j];
            const dx = n2.x - n1.x;
            const dy = n2.y - n1.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = repulsion / (dist * dist);
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            n1.vx -= fx;
            n1.vy -= fy;
            n2.vx += fx;
            n2.vy += fy;
          }
        }

        // Attraction along edges
        edges.forEach(e => {
          const source = nodeMap.get(e.source);
          const target = nodeMap.get(e.target);
          if (!source || !target) return;

          const dx = target.x - source.x;
          const dy = target.y - source.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = dist * attraction;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          source.vx += fx;
          source.vy += fy;
          target.vx -= fx;
          target.vy -= fy;
        });

        // Center gravity
        nodeList.forEach(n => {
          n.vx -= n.x * centerGravity;
          n.vy -= n.y * centerGravity;
        });

        // Update positions
        return currentNodes.map(n => {
          const simNode = nodeMap.get(n.id);
          simNode.x += simNode.vx;
          simNode.y += simNode.vy;
          simNode.vx *= damping;
          simNode.vy *= damping;

          return {
            ...n,
            position: { x: simNode.x, y: simNode.y },
            vx: simNode.vx,
            vy: simNode.vy
          };
        });
      });
    };

    intervalRef.current = setInterval(simulate, 50);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [physicsEnabled, nodes.length, edges, setNodes]);

  const filteredData = useMemo(() => {
    if (!searchTerm.trim()) return { nodes, edges };

    const term = searchTerm.toLowerCase();
    const matchedNodes = nodes.filter(n =>
      n.id.toLowerCase().includes(term) ||
      n.data.label.toLowerCase().includes(term)
    );
    const matchedIds = new Set(matchedNodes.map(n => n.id));

    // Include nodes connected to matches
    edges.forEach(e => {
      if (matchedIds.has(e.source)) {
        const targetNode = nodes.find(n => n.id === e.target);
        if (targetNode) matchedNodes.push(targetNode);
      }
      if (matchedIds.has(e.target)) {
        const sourceNode = nodes.find(n => n.id === e.source);
        if (sourceNode) matchedNodes.push(sourceNode);
      }
    });

    const uniqueNodes = Array.from(new Map(matchedNodes.map(n => [n.id, n])).values());
    const uniqueIds = new Set(uniqueNodes.map(n => n.id));

    const filteredEdges = edges.filter(e =>
      uniqueIds.has(e.source) && uniqueIds.has(e.target)
    );

    return { nodes: uniqueNodes, edges: filteredEdges };
  }, [nodes, edges, searchTerm]);

  const onNodeClick = useCallback(
    (_evt, node) => {
      if (node.data.isExternal) return; // Don't navigate for external packages

      const filename = node.id;
      const runIdForFile = runIdByFileRef.current.get(filename) ?? runs?.[0]?.run_id;

      if (runIdForFile != null) {
        onOpenFile?.(filename, runIdForFile);
      }
    },
    [onOpenFile, runs]
  );

  const hasData = nodes && nodes.length > 0;

  const stats = useMemo(() => {
    if (!hasData) return null;
    const externalCount = nodes.filter(n => n.data.isExternal).length;
    return {
      total: nodes.length,
      internal: nodes.length - externalCount,
      external: externalCount,
      edges: edges.length
    };
  }, [nodes, edges, hasData]);

  return (
    <div
      style={{
        height,
        width: "100%",
        display: "flex",
        flexDirection: "column",
        background: COLORS.background,
        borderRadius: '8px',
        overflow: 'hidden'
      }}
    >
      {/* Controls header - dark theme */}
      <div style={{
        padding: "10px 16px",
        background: '#333333',
        borderBottom: '1px solid #444',
        display: 'flex',
        gap: '16px',
        alignItems: 'center',
        flexWrap: 'wrap'
      }}>
        {loading && <span style={{ fontSize: 13, color: COLORS.text }}>Loading file dependencies...</span>}
        {!loading && error && (
          <span style={{ color: "#ff6b6b", fontSize: 13 }}>Error: {error}</span>
        )}
        {!loading && !error && hasData && (
          <>
            <input
              type="text"
              placeholder="Search files..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                padding: '6px 10px',
                fontSize: 12,
                border: '1px solid #555',
                borderRadius: '4px',
                minWidth: '180px',
                background: '#444',
                color: COLORS.text,
              }}
            />

            <button
              onClick={() => setPhysicsEnabled(!physicsEnabled)}
              style={{
                padding: '6px 12px',
                fontSize: 12,
                border: 'none',
                borderRadius: '4px',
                background: physicsEnabled ? '#4CAF50' : '#555',
                color: COLORS.text,
                cursor: 'pointer',
                transition: 'background 0.2s'
              }}
            >
              Physics: {physicsEnabled ? 'ON' : 'OFF'}
            </button>

            {stats && (
              <div style={{
                fontSize: 11,
                color: '#aaa',
                marginLeft: 'auto',
                display: 'flex',
                gap: '12px'
              }}>
                <span style={{ color: COLORS.internal }}>● {stats.internal} internal</span>
                <span style={{ color: COLORS.external }}>■ {stats.external} external</span>
                <span style={{ color: '#888' }}>─ {stats.edges} edges</span>
              </div>
            )}
          </>
        )}
        {!loading && !error && !hasData && (
          <span style={{ fontSize: 13, color: COLORS.text }}>No dependencies found for this run.</span>
        )}
      </div>

      {/* Graph area */}
      <div style={{ flex: 1, position: 'relative' }}>
        {hasData && (
          <ReactFlow
            nodes={filteredData.nodes}
            edges={filteredData.edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            fitView
            minZoom={0.1}
            maxZoom={2}
            nodesDraggable={true}
            style={{ background: COLORS.background }}
          >
            <MiniMap
              nodeColor={(node) => node.data.isExternal ? COLORS.external : COLORS.internal}
              maskColor="rgba(0,0,0,0.8)"
              style={{ background: '#333' }}
            />
            <Controls
              style={{
                button: { background: '#444', color: COLORS.text, border: '1px solid #555' }
              }}
            />
            <Background color="#444" gap={20} />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
