import React, {
  useMemo,
  useCallback,
  useEffect,
  useState
} from "react";
import ReactFlow, { Background, Controls, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

const DEP_FIELD = "dependencies";

function isTestFile(name) {
  if (!name) return false;

  // Normalize path separators to forward slashes
  const lower = name.toLowerCase().replace(/\\/g, "/");

  const filename = lower.split("/").pop() || lower;

  // Classic pytest-style names
  if (filename.startsWith("test_") || filename.endsWith("_test.py")) {
    return true;
  }

  // Anything under a tests/ directory (with or without leading slash)
  if (lower.includes("/tests/") || lower.startsWith("tests/")) {
    return true;
  }

  return false;
}


/**
 * Hierarchical layered layout using Sugiyama algorithm principles
 * Groups nodes by their dependency level and spreads them horizontally
 */
function applyHierarchicalLayout(nodes, edges) {
  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const inDegree = new Map();
  const outEdges = new Map();
  
  // Initialize
  nodes.forEach(n => {
    inDegree.set(n.id, 0);
    outEdges.set(n.id, []);
  });
  
  // Build adjacency info
  edges.forEach(e => {
    inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
    outEdges.get(e.source)?.push(e.target);
  });
  
  // Topological sort to assign layers
  const layers = [];
  const queue = [];
  const layerMap = new Map();
  
  // Start with nodes that have no incoming edges
  inDegree.forEach((degree, nodeId) => {
    if (degree === 0) {
      queue.push(nodeId);
      layerMap.set(nodeId, 0);
    }
  });
  
  while (queue.length > 0) {
    const currentLayer = [];
    const layerSize = queue.length;
    
    for (let i = 0; i < layerSize; i++) {
      const nodeId = queue.shift();
      currentLayer.push(nodeId);
      
      const neighbors = outEdges.get(nodeId) || [];
      neighbors.forEach(neighbor => {
        const newDegree = inDegree.get(neighbor) - 1;
        inDegree.set(neighbor, newDegree);
        
        if (newDegree === 0) {
          const currentNodeLayer = layerMap.get(nodeId);
          layerMap.set(neighbor, currentNodeLayer + 1);
          queue.push(neighbor);
        }
      });
    }
    
    if (currentLayer.length > 0) {
      layers.push(currentLayer);
    }
  }
  
  // Handle remaining nodes (cycles or disconnected)
  const processed = new Set(layerMap.keys());
  const remaining = nodes.filter(n => !processed.has(n.id));
  if (remaining.length > 0) {
    layers.push(remaining.map(n => n.id));
    remaining.forEach(n => layerMap.set(n.id, layers.length - 1));
  }
  
  // Position nodes
  const layerWidth = 400;
  const nodeHeight = 80;
  const nodeWidth = 250;
  
  return nodes.map(n => {
    const layer = layerMap.get(n.id) || 0;
    const layerNodes = layers[layer] || [];
    const indexInLayer = layerNodes.indexOf(n.id);
    const layerSize = layerNodes.length;
    
    // Center nodes vertically within each layer
    const totalHeight = layerSize * nodeHeight;
    const startY = -totalHeight / 2;
    
    return {
      ...n,
      position: {
        x: layer * layerWidth,
        y: startY + indexInLayer * nodeHeight
      },
      style: {
        width: nodeWidth,
        fontSize: '11px',
        padding: '8px',
        background: isTestFile(n.id) ? '#e3f2fd' : '#fff3e0',
        border: `2px solid ${isTestFile(n.id) ? '#1976d2' : '#f57c00'}`,
        borderRadius: '6px'
      }
    };
  });
}

/**
 * Force-directed layout for better visualization
 */
function applyForceLayout(nodes, edges) {
  const nodeMap = new Map(nodes.map(n => [n.id, { ...n, vx: 0, vy: 0 }]));
  const iterations = 100;
  const repulsion = 50000;
  const attraction = 0.01;
  const damping = 0.8;
  
  // Initialize with category-based positions
  const testNodes = [];
  const sourceNodes = [];
  
  nodes.forEach(n => {
    if (isTestFile(n.id)) testNodes.push(n.id);
    else sourceNodes.push(n.id);
  });
  
  sourceNodes.forEach((id, i) => {
    const node = nodeMap.get(id);
    node.x = -300;
    node.y = i * 100 - (sourceNodes.length * 50);
  });
  
  testNodes.forEach((id, i) => {
    const node = nodeMap.get(id);
    node.x = 300;
    node.y = i * 100 - (testNodes.length * 50);
  });
  
  // Simulation
  for (let iter = 0; iter < iterations; iter++) {
    // Repulsion between all nodes
    const nodeList = Array.from(nodeMap.values());
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
    
    // Update positions
    nodeList.forEach(n => {
      n.x += n.vx;
      n.y += n.vy;
      n.vx *= damping;
      n.vy *= damping;
    });
  }
  
  return nodes.map(n => {
    const simNode = nodeMap.get(n.id);
    return {
      ...n,
      position: { x: simNode.x, y: simNode.y },
      style: {
        fontSize: '11px',
        padding: '8px',
        background: isTestFile(n.id) ? '#e3f2fd' : '#fff3e0',
        border: `2px solid ${isTestFile(n.id) ? '#1976d2' : '#f57c00'}`,
        borderRadius: '6px'
      }
    };
  });
}

/**
 * Build directed graph with edge bundling for high-degree nodes
 */
function buildGraphFromRuns(runs, layoutType = 'hierarchical') {
  const runIdByFile = new Map();
  const allFiles = new Set();
  const rawEdges = [];
  const seenDirected = new Set();

  for (const run of runs || []) {
    for (const f of run.files || []) {
      const file = f.filename;
      if (!file) continue;

      allFiles.add(file);

      if (!runIdByFile.has(file)) {
        runIdByFile.set(file, run.run_id);
      }

      const deps = Array.isArray(f[DEP_FIELD]) ? f[DEP_FIELD] : [];
      for (const dep of deps) {
        if (!dep || dep === file) continue;
        allFiles.add(dep);

        const fileIsTest = isTestFile(file);
        const depIsTest = isTestFile(dep);

        let source, target;

        if (!fileIsTest && depIsTest) {
          source = file;
          target = dep;
        } else if (fileIsTest && !depIsTest) {
          source = dep;
          target = file;
        } else {
          if (file <= dep) {
            source = file;
            target = dep;
          } else {
            source = dep;
            target = file;
          }
        }

        const directedKey = `${source}|${target}`;
        if (seenDirected.has(directedKey)) continue;
        seenDirected.add(directedKey);

        rawEdges.push({ source, target });
      }
    }
  }

  // Calculate node degrees for styling
  const outDegree = new Map();
  const inDegree = new Map();
  
  allFiles.forEach(f => {
    outDegree.set(f, 0);
    inDegree.set(f, 0);
  });
  
  rawEdges.forEach(e => {
    outDegree.set(e.source, (outDegree.get(e.source) || 0) + 1);
    inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
  });

  // Create nodes
  const names = Array.from(allFiles);
  const nodes = names.map((name) => {
    const degree = (outDegree.get(name) || 0) + (inDegree.get(name) || 0);
    return {
      id: name,
      data: { 
        label: name.split('/').pop() || name, // Show only filename
        fullPath: name,
        degree
      },
      position: { x: 0, y: 0 }
    };
  });

  // Create edges with styling based on importance
  const edges = rawEdges.map(e => {
    const sourceOut = outDegree.get(e.source) || 0;
    const targetIn = inDegree.get(e.target) || 0;
    
    // Hub nodes (high degree) get different edge styling
    const isHighDegree = sourceOut > 10 || targetIn > 10;
    
    return {
      id: `${e.source}|${e.target}`,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: isHighDegree,
      style: {
        stroke: isHighDegree ? '#ff6b6b' : '#999',
        strokeWidth: isHighDegree ? 2 : 1,
        opacity: isHighDegree ? 0.6 : 0.3
      }
    };
  });

  const layoutedNodes = applyHierarchicalLayout(nodes, edges);


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

 const { nodes, edges, runIdByFile } = useMemo(
    () => buildGraphFromRuns(runs),
    [runs]
  );

  const filteredData = useMemo(() => {
    if (!searchTerm.trim()) return { nodes, edges };
    
    const term = searchTerm.toLowerCase();
    const matchedNodes = nodes.filter(n => 
      n.id.toLowerCase().includes(term) ||
      n.data.label.toLowerCase().includes(term)
    );
    const matchedIds = new Set(matchedNodes.map(n => n.id));
    
    const filteredEdges = edges.filter(e => 
      matchedIds.has(e.source) || matchedIds.has(e.target)
    );
    
    return { nodes: matchedNodes, edges: filteredEdges };
  }, [nodes, edges, searchTerm]);

  const onNodeClick = useCallback(
    (_evt, node) => {
      const filename = node.id;
      const runIdForFile = runIdByFile.get(filename) ?? runs?.[0]?.run_id;

      if (runIdForFile != null) {
        onOpenFile?.(filename, runIdForFile);
      }
    },
    [onOpenFile, runIdByFile, runs]
  );

  const hasData = nodes && nodes.length > 0;
  const stats = useMemo(() => {
    if (!hasData) return null;
    const testCount = nodes.filter(n => isTestFile(n.id)).length;
    return {
      total: nodes.length,
      tests: testCount,
      source: nodes.length - testCount,
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
        background: '#fafafa'
      }}
    >
      {/* Controls header */}
      <div style={{ 
        padding: "12px 16px", 
        background: 'white',
        borderBottom: '1px solid #e0e0e0',
        display: 'flex',
        gap: '16px',
        alignItems: 'center',
        flexWrap: 'wrap'
      }}>
        {loading && <span style={{ fontSize: 13 }}>Loading file dependencies…</span>}
        {!loading && error && (
          <span style={{ color: "red", fontSize: 13 }}>Error: {error}</span>
        )}
        {!loading && !error && hasData && (
          <>
          
            
            <input
              type="text"
              placeholder="Search files..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                padding: '4px 8px',
                fontSize: 12,
                border: '1px solid #ccc',
                borderRadius: '4px',
                minWidth: '200px'
              }}
            />
            
            {stats && (
              <div style={{ 
                fontSize: 11, 
                color: '#666',
                marginLeft: 'auto',
                display: 'flex',
                gap: '12px'
              }}>
                <span>📁 {stats.source} source</span>
                <span>🧪 {stats.tests} tests</span>
                <span>🔗 {stats.edges} connections</span>
              </div>
            )}
          </>
        )}
        {!loading && !error && !hasData && (
          <span style={{ fontSize: 13 }}>No dependencies found for this run.</span>
        )}
      </div>

      <div style={{ flex: 1, position: 'relative' }}>
        {hasData && (
          <ReactFlow
            nodes={filteredData.nodes}
            edges={filteredData.edges}
            onNodeClick={onNodeClick}
            fitView
            minZoom={0.1}
            maxZoom={2}
            defaultEdgeOptions={{
              type: 'smoothstep'
            }}
          >
            <MiniMap 
              nodeColor={(node) => isTestFile(node.id) ? '#1976d2' : '#f57c00'}
              style={{ background: '#f5f5f5' }}
            />
            <Controls />
            <Background color="#ddd" gap={16} />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}