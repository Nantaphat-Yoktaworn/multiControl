/**
 * MultiControl 2D Screen Layout Studio & Real-Time Cursor Radar
 */

(() => {
  const canvas = document.getElementById("layoutCanvas");
  const ctx = canvas.getContext("2d");
  const wrapper = document.getElementById("canvasWrapper");

  let ws = null;
  let state = {
    displays: [],
    devices: [],
    settings: {
      swap_cmd_ctrl: true,
      swap_opt_win: false,
      clipboard_sync: true,
      invert_scroll_on_windows: false,
      scroll_speed: 1.0,
      mouse_speed_windows: 1.0,
      corner_barrier_px: 0.0,
      edge_snap_tolerance_px: 18.0,
    },
    cursor: {
      device_id: "",
      display_id: "",
      canvas_x: 400,
      canvas_y: 400,
      os_x: 400,
      os_y: 400,
      updated_at: 0,
    },
    server_info: {
      lan_ip: window.location.hostname || "192.168.1.100",
      tailscale_ip: "100.64.0.10",
      http_port: window.location.port || 8765,
    },
  };

  // Viewport transform (canvas units -> screen pixels)
  let view = {
    scale: 0.22,
    offsetX: 120,
    offsetY: 120,
    initialized: false,
  };

  // Interaction state
  let selectedId = null;
  let dragMode = null; // "move" | "resize" | "pan" | "simulate"
  let dragTarget = null;
  let dragStartMouse = { x: 0, y: 0 };
  let dragStartRect = { x: 0, y: 0, w: 0, h: 0 };
  let activeSnapLines = [];
  let simulateMode = false;
  let simCursor = null; // { cx, cy, disp, ox, oy, portalHit }
  let saveDebounceTimer = null;

  // -------------------------------------------------------------------------
  // WebSocket Connection
  // -------------------------------------------------------------------------
  function connectWebSocket() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/ui`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      document.getElementById("hubStatusDot").classList.add("online");
      document.getElementById("hubStatusText").textContent = "Hub Online";
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "cluster_state") {
          const isFirst = !view.initialized;
          state.displays = msg.displays || [];
          state.devices = msg.devices || [];
          if (msg.settings) state.settings = msg.settings;
          if (msg.cursor) state.cursor = msg.cursor;
          if (msg.server_info) state.server_info = msg.server_info;

          if (!selectedId && state.displays.length > 0) {
            selectedId = state.displays[0].id;
          }
          if (isFirst && state.displays.length > 0) {
            centerCanvasView();
            view.initialized = true;
          }
          syncUIFromState();
          render();
        } else if (msg.type === "cursor_telemetry") {
          state.cursor = msg.cursor;
          updateLiveCursorReadout();
          render();
        }
      } catch (err) {
        console.error("WS parse error:", err);
      }
    };

    ws.onclose = () => {
      document.getElementById("hubStatusDot").classList.remove("online");
      document.getElementById("hubStatusText").textContent = "Reconnecting...";
      setTimeout(connectWebSocket, 2000);
    };
  }

  function scheduleSaveLayout(showNotification = false) {
    if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
    saveDebounceTimer = setTimeout(() => {
      saveLayoutNow(showNotification);
    }, 180);
  }

  function saveLayoutNow(showNotification = true) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "save_layout",
          displays: state.displays,
          settings: state.settings,
        })
      );
      if (showNotification) showToast("Layout saved & broadcast to Mac and Windows!");
    }
  }

  function showToast(text) {
    const t = document.getElementById("toast");
    t.textContent = text;
    t.classList.remove("hidden");
    setTimeout(() => t.classList.add("hidden"), 2400);
  }

  // -------------------------------------------------------------------------
  // Coordinate Helpers
  // -------------------------------------------------------------------------
  function screenToWorld(sx, sy) {
    return {
      x: (sx - view.offsetX) / view.scale,
      y: (sy - view.offsetY) / view.scale,
    };
  }

  function worldToScreen(wx, wy) {
    return {
      x: wx * view.scale + view.offsetX,
      y: wy * view.scale + view.offsetY,
    };
  }

  function centerCanvasView() {
    if (!state.displays.length) return;
    let minX = Infinity,
      minY = Infinity,
      maxX = -Infinity,
      maxY = -Infinity;
    for (const d of state.displays) {
      minX = Math.min(minX, d.canvas_x);
      minY = Math.min(minY, d.canvas_y);
      maxX = Math.max(maxX, d.canvas_x + d.canvas_width);
      maxY = Math.max(maxY, d.canvas_y + d.canvas_height);
    }
    const totalW = Math.max(800, maxX - minX);
    const totalH = Math.max(500, maxY - minY);
    const pad = 140;
    const w = wrapper.clientWidth || 900;
    const h = wrapper.clientHeight || 600;
    const scaleX = (w - pad * 2) / totalW;
    const scaleY = (h - pad * 2) / totalH;
    view.scale = Math.max(0.08, Math.min(0.45, Math.min(scaleX, scaleY)));
    view.offsetX = (w - totalW * view.scale) / 2 - minX * view.scale;
    view.offsetY = (h - totalH * view.scale) / 2 - minY * view.scale;
    document.getElementById("zoomLabel").textContent = `${Math.round(view.scale * 400)}%`;
  }

  // -------------------------------------------------------------------------
  // Active Transition Portal Detection (Shared Edges between Displays)
  // -------------------------------------------------------------------------
  function computeActivePortals() {
    const portals = [];
    const tol = (state.settings && state.settings.edge_snap_tolerance_px) || 18.0;
    const list = state.displays;

    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i];
        const b = list[j];
        const aRight = a.canvas_x + a.canvas_width;
        const aBottom = a.canvas_y + a.canvas_height;
        const bRight = b.canvas_x + b.canvas_width;
        const bBottom = b.canvas_y + b.canvas_height;

        // Vertical shared edge (a's Right touches b's Left, or b's Right touches a's Left)
        if (Math.abs(aRight - b.canvas_x) <= tol) {
          const y1 = Math.max(a.canvas_y, b.canvas_y);
          const y2 = Math.min(aBottom, bBottom);
          if (y2 - y1 > 20) {
            const xMid = (aRight + b.canvas_x) / 2;
            portals.push({
              orientation: "vertical",
              x1: xMid,
              y1,
              x2: xMid,
              y2,
              length: Math.round(y2 - y1),
              crossMachine: a.device_id !== b.device_id,
              a,
              b,
            });
          }
        } else if (Math.abs(bRight - a.canvas_x) <= tol) {
          const y1 = Math.max(a.canvas_y, b.canvas_y);
          const y2 = Math.min(aBottom, bBottom);
          if (y2 - y1 > 20) {
            const xMid = (bRight + a.canvas_x) / 2;
            portals.push({
              orientation: "vertical",
              x1: xMid,
              y1,
              x2: xMid,
              y2,
              length: Math.round(y2 - y1),
              crossMachine: a.device_id !== b.device_id,
              a: b,
              b: a,
            });
          }
        }

        // Horizontal shared edge (a's Bottom touches b's Top, or b's Bottom touches a's Top)
        if (Math.abs(aBottom - b.canvas_y) <= tol) {
          const x1 = Math.max(a.canvas_x, b.canvas_x);
          const x2 = Math.min(aRight, bRight);
          if (x2 - x1 > 20) {
            const yMid = (aBottom + b.canvas_y) / 2;
            portals.push({
              orientation: "horizontal",
              x1,
              y1: yMid,
              x2,
              y2: yMid,
              length: Math.round(x2 - x1),
              crossMachine: a.device_id !== b.device_id,
              a,
              b,
            });
          }
        } else if (Math.abs(bBottom - a.canvas_y) <= tol) {
          const x1 = Math.max(a.canvas_x, b.canvas_x);
          const x2 = Math.min(aRight, bRight);
          if (x2 - x1 > 20) {
            const yMid = (bBottom + a.canvas_y) / 2;
            portals.push({
              orientation: "horizontal",
              x1,
              y1: yMid,
              x2,
              y2: yMid,
              length: Math.round(x2 - x1),
              crossMachine: a.device_id !== b.device_id,
              a: b,
              b: a,
            });
          }
        }
      }
    }
    return portals;
  }

  // -------------------------------------------------------------------------
  // Magnetic Snapping Logic
  // -------------------------------------------------------------------------
  function applyMagneticSnap(movingDisp, rawX, rawY) {
    const snapDist = 26 / view.scale;
    let snappedX = rawX;
    let snappedY = rawY;
    const guides = [];

    const mW = movingDisp.canvas_width;
    const mH = movingDisp.canvas_height;

    for (const other of state.displays) {
      if (other.id === movingDisp.id) continue;

      const oL = other.canvas_x;
      const oR = other.canvas_x + other.canvas_width;
      const oCX = other.canvas_x + other.canvas_width / 2;
      const oT = other.canvas_y;
      const oB = other.canvas_y + other.canvas_height;
      const oCY = other.canvas_y + other.canvas_height / 2;

      // X-axis snap targets: [candidateX, guideX]
      const xCandidates = [
        [oR, oR],             // Left of moving -> Right of other
        [oL - mW, oL],        // Right of moving -> Left of other
        [oL, oL],             // Align Left edges
        [oR - mW, oR],        // Align Right edges
        [oCX - mW / 2, oCX],  // Align horizontal centers
      ];
      for (const [candX, gx] of xCandidates) {
        if (Math.abs(snappedX - candX) < snapDist) {
          snappedX = candX;
          guides.push({ axis: "x", pos: gx });
          break;
        }
      }

      // Y-axis snap targets: [candidateY, guideY]
      const yCandidates = [
        [oB, oB],             // Top of moving -> Bottom of other
        [oT - mH, oT],        // Bottom of moving -> Top of other
        [oT, oT],             // Align Top edges
        [oB - mH, oB],        // Align Bottom edges
        [oCY - mH / 2, oCY],  // Align vertical centers
      ];
      for (const [candY, gy] of yCandidates) {
        if (Math.abs(snappedY - candY) < snapDist) {
          snappedY = candY;
          guides.push({ axis: "y", pos: gy });
          break;
        }
      }
    }

    return { x: Math.round(snappedX), y: Math.round(snappedY), guides };
  }

  // -------------------------------------------------------------------------
  // Canvas Rendering
  // -------------------------------------------------------------------------
  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = wrapper.clientWidth;
    const h = wrapper.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    render();
  }

  function render() {
    const w = wrapper.clientWidth;
    const h = wrapper.clientHeight;
    ctx.clearRect(0, 0, w, h);

    // 1. Background Grid
    drawGrid(w, h);

    // 2. Magnetic alignment snap guides
    for (const g of activeSnapLines) {
      ctx.save();
      ctx.strokeStyle = "rgba(56, 189, 248, 0.7)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      if (g.axis === "x") {
        const sx = worldToScreen(g.pos, 0).x;
        ctx.moveTo(sx, 0);
        ctx.lineTo(sx, h);
      } else {
        const sy = worldToScreen(0, g.pos).y;
        ctx.moveTo(0, sy);
        ctx.lineTo(w, sy);
      }
      ctx.stroke();
      ctx.restore();
    }

    // 3. Draw Each Monitor Box
    for (const d of state.displays) {
      drawDisplayBox(d);
    }

    // 4. Draw Glowing Transition Portals along shared borders
    const portals = computeActivePortals();
    const badge = document.getElementById("activePortalBadge");
    if (portals.length > 0) {
      badge.textContent = `✓ ${portals.length} Seamless Edge Portal${portals.length > 1 ? "s" : ""} Active`;
      badge.style.color = "#34d399";
      badge.style.borderColor = "rgba(16, 185, 129, 0.4)";
    } else {
      badge.textContent = "⚠ Move monitors until edges touch to create a crossing portal";
      badge.style.color = "#fbbf24";
      badge.style.borderColor = "rgba(245, 158, 11, 0.4)";
    }

    for (const p of portals) {
      drawPortal(p);
    }

    // 5. Draw Live Cursor or Simulated Cursor
    drawCursorIndicators(portals);
  }

  function drawGrid(w, h) {
    const stepWorld = 200;
    const stepScreen = stepWorld * view.scale;
    if (stepScreen < 14) return;

    ctx.save();
    ctx.strokeStyle = "rgba(51, 65, 85, 0.28)";
    ctx.lineWidth = 1;

    const startX = ((view.offsetX % stepScreen) + stepScreen) % stepScreen;
    const startY = ((view.offsetY % stepScreen) + stepScreen) % stepScreen;

    ctx.beginPath();
    for (let x = startX; x < w; x += stepScreen) {
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
    }
    for (let y = startY; y < h; y += stepScreen) {
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawDisplayBox(d) {
    const tl = worldToScreen(d.canvas_x, d.canvas_y);
    const sw = d.canvas_width * view.scale;
    const sh = d.canvas_height * view.scale;
    const isMac = d.os_type === "darwin";
    const isSelected = d.id === selectedId;

    ctx.save();

    // Fill & Border
    const fillGrad = ctx.createLinearGradient(tl.x, tl.y, tl.x + sw, tl.y + sh);
    if (isMac) {
      fillGrad.addColorStop(0, "rgba(79, 70, 229, 0.24)");
      fillGrad.addColorStop(1, "rgba(30, 27, 75, 0.72)");
    } else {
      fillGrad.addColorStop(0, "rgba(2, 132, 199, 0.24)");
      fillGrad.addColorStop(1, "rgba(12, 74, 110, 0.72)");
    }

    ctx.fillStyle = fillGrad;
    ctx.strokeStyle = isSelected
      ? "#f8fafc"
      : isMac
      ? "#818cf8"
      : "#38bdf8";
    ctx.lineWidth = isSelected ? 2.5 : 1.8;

    ctx.beginPath();
    ctx.roundRect(tl.x, tl.y, sw, sh, 8);
    ctx.fill();
    ctx.stroke();

    // Top OS Header Bar inside monitor
    ctx.fillStyle = isMac ? "rgba(129, 140, 248, 0.22)" : "rgba(56, 189, 248, 0.22)";
    ctx.beginPath();
    ctx.roundRect(tl.x, tl.y, sw, Math.min(28, sh * 0.2), [8, 8, 0, 0]);
    ctx.fill();

    // OS Badge + Device Name
    ctx.fillStyle = "#f8fafc";
    ctx.font = "600 12px -apple-system, BlinkMacSystemFont, sans-serif";
    const icon = isMac ? "" : "⊞";
    ctx.fillText(`${icon} ${d.device_name} — ${d.name}`, tl.x + 10, tl.y + 18);

    // Center Resolution & Physical Scale Info
    const scalePct = Math.round((d.canvas_width / Math.max(1, d.os_width)) * 100);
    ctx.fillStyle = "rgba(248, 250, 252, 0.9)";
    ctx.font = "700 15px ui-monospace, SFMono-Regular, monospace";
    ctx.textAlign = "center";
    ctx.fillText(
      `${Math.round(d.os_width)} × ${Math.round(d.os_height)}`,
      tl.x + sw / 2,
      tl.y + sh / 2 - 4
    );

    ctx.fillStyle = "#94a3b8";
    ctx.font = "500 11px -apple-system, BlinkMacSystemFont, sans-serif";
    const scaleNote =
      scalePct === 100
        ? `Native 1:1 (${d.scale_factor || 1}x DPI)`
        : `Physical Size Scale: ${scalePct}% (${Math.round(d.canvas_width)}×${Math.round(d.canvas_height)})`;
    ctx.fillText(scaleNote, tl.x + sw / 2, tl.y + sh / 2 + 16);

    // Bottom-right Resize Handle
    const handleSize = 14;
    ctx.fillStyle = isSelected ? "#f8fafc" : "rgba(148, 163, 184, 0.7)";
    ctx.beginPath();
    ctx.moveTo(tl.x + sw, tl.y + sh - handleSize);
    ctx.lineTo(tl.x + sw, tl.y + sh);
    ctx.lineTo(tl.x + sw - handleSize, tl.y + sh);
    ctx.closePath();
    ctx.fill();

    ctx.restore();
  }

  function drawPortal(p) {
    const p1 = worldToScreen(p.x1, p.y1);
    const p2 = worldToScreen(p.x2, p.y2);
    const barrierPx = ((state.settings && state.settings.corner_barrier_px) || 0) * view.scale;

    ctx.save();
    ctx.strokeStyle = "#10b981";
    ctx.shadowColor = "#10b981";
    ctx.shadowBlur = 12;
    ctx.lineWidth = 5;
    ctx.lineCap = "round";

    ctx.beginPath();
    if (p.orientation === "vertical") {
      ctx.moveTo(p1.x, p1.y + barrierPx);
      ctx.lineTo(p2.x, p2.y - barrierPx);
    } else {
      ctx.moveTo(p1.x + barrierPx, p1.y);
      ctx.lineTo(p2.x - barrierPx, p2.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawCursorIndicators(portals) {
    // 1. Live cursor from Mac agent (if updated in last 15 seconds)
    const nowSec = Date.now() / 1000;
    if (state.cursor && state.cursor.updated_at && nowSec - state.cursor.updated_at < 15) {
      const pt = worldToScreen(state.cursor.canvas_x, state.cursor.canvas_y);
      ctx.save();
      ctx.fillStyle = "#10b981";
      ctx.shadowColor = "#10b981";
      ctx.shadowBlur = 14;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
    }

    // 2. Interactive Test Cursor Crossing Simulator
    if (simulateMode && simCursor && simCursor.disp) {
      const pt = worldToScreen(simCursor.cx, simCursor.cy);
      ctx.save();
      ctx.fillStyle = "#f43f5e";
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();

      // Project horizontal & vertical crossing rays to any adjacent portal
      for (const p of portals) {
        if (p.orientation === "vertical" && simCursor.cy >= p.y1 && simCursor.cy <= p.y2) {
          const targetDisp = p.a.id === simCursor.disp.id ? p.b : p.b.id === simCursor.disp.id ? p.a : null;
          if (targetDisp) {
            const portalScreen = worldToScreen(p.x1, simCursor.cy);
            ctx.strokeStyle = "rgba(244, 63, 94, 0.75)";
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(pt.x, pt.y);
            ctx.lineTo(portalScreen.x, portalScreen.y);
            ctx.stroke();

            // Compute exact OS coordinate where cursor enters targetDisp
            const u =
              targetDisp.canvas_x >= simCursor.disp.canvas_x
                ? 0
                : 1;
            const v =
              (simCursor.cy - targetDisp.canvas_y) /
              Math.max(1, targetDisp.canvas_height);
            const entryOx = Math.round(
              targetDisp.os_x + u * (targetDisp.os_width - 1)
            );
            const entryOy = Math.round(
              targetDisp.os_y + Math.max(0, Math.min(1, v)) * (targetDisp.os_height - 1)
            );

            ctx.setLineDash([]);
            ctx.fillStyle = "#f43f5e";
            ctx.beginPath();
            ctx.arc(portalScreen.x, portalScreen.y, 5, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = "#0f172a";
            ctx.strokeStyle = "#f43f5e";
            ctx.lineWidth = 1.5;
            const tagText = `→ Enters ${targetDisp.name} at (${entryOx}, ${entryOy})`;
            ctx.font = "600 11px ui-monospace, SFMono-Regular, monospace";
            const tw = ctx.measureText(tagText).width + 14;
            ctx.beginPath();
            ctx.roundRect(portalScreen.x + 10, portalScreen.y - 12, tw, 24, 5);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = "#f8fafc";
            ctx.fillText(tagText, portalScreen.x + 17, portalScreen.y + 4);
          }
        }
      }
      ctx.restore();
    }
  }

  // -------------------------------------------------------------------------
  // Canvas Pointer Events (Move, Resize, Pan, Simulate)
  // -------------------------------------------------------------------------
  function hitTestDisplays(wx, wy) {
    for (let i = state.displays.length - 1; i >= 0; i--) {
      const d = state.displays[i];
      if (
        wx >= d.canvas_x &&
        wx <= d.canvas_x + d.canvas_width &&
        wy >= d.canvas_y &&
        wy <= d.canvas_y + d.canvas_height
      ) {
        const handleRadius = 24 / view.scale;
        const inResizeCorner =
          wx >= d.canvas_x + d.canvas_width - handleRadius &&
          wy >= d.canvas_y + d.canvas_height - handleRadius;
        return { display: d, resize: inResizeCorner };
      }
    }
    return null;
  }

  canvas.addEventListener("mousedown", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const sx = ev.clientX - rect.left;
    const sy = ev.clientY - rect.top;
    const world = screenToWorld(sx, sy);

    const hit = hitTestDisplays(world.x, world.y);
    if (hit) {
      selectedId = hit.display.id;
      dragTarget = hit.display;
      dragMode = hit.resize ? "resize" : "move";
      dragStartMouse = { x: world.x, y: world.y };
      dragStartRect = {
        x: hit.display.canvas_x,
        y: hit.display.canvas_y,
        w: hit.display.canvas_width,
        h: hit.display.canvas_height,
      };
      renderSelectedMonitorInspector();
      render();
    } else {
      dragMode = "pan";
      dragStartMouse = { x: sx, y: sy };
      dragStartRect = { x: view.offsetX, y: view.offsetY, w: 0, h: 0 };
    }
  });

  window.addEventListener("mousemove", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const sx = ev.clientX - rect.left;
    const sy = ev.clientY - rect.top;
    const world = screenToWorld(sx, sy);

    if (simulateMode && !dragMode) {
      const hit = hitTestDisplays(world.x, world.y);
      if (hit) {
        const d = hit.display;
        const u = (world.x - d.canvas_x) / Math.max(1, d.canvas_width);
        const v = (world.y - d.canvas_y) / Math.max(1, d.canvas_height);
        const ox = Math.round(d.os_x + u * (d.os_width - 1));
        const oy = Math.round(d.os_y + v * (d.os_height - 1));
        simCursor = { cx: world.x, cy: world.y, disp: d, ox, oy };
        document.getElementById(
          "liveCursorReadout"
        ).textContent = `Simulated: ${d.name} (${ox}, ${oy})`;
      } else {
        simCursor = null;
      }
      render();
    }

    if (!dragMode) return;

    if (dragMode === "pan") {
      view.offsetX = dragStartRect.x + (sx - dragStartMouse.x);
      view.offsetY = dragStartRect.y + (sy - dragStartMouse.y);
      render();
      return;
    }

    if (dragMode === "move" && dragTarget) {
      const rawX = dragStartRect.x + (world.x - dragStartMouse.x);
      const rawY = dragStartRect.y + (world.y - dragStartMouse.y);
      if (ev.altKey) {
        dragTarget.canvas_x = Math.round(rawX);
        dragTarget.canvas_y = Math.round(rawY);
        activeSnapLines = [];
      } else {
        const snapped = applyMagneticSnap(dragTarget, rawX, rawY);
        dragTarget.canvas_x = snapped.x;
        dragTarget.canvas_y = snapped.y;
        activeSnapLines = snapped.guides;
      }
      renderSelectedMonitorInspector();
      render();
    } else if (dragMode === "resize" && dragTarget) {
      const aspect = dragTarget.os_width / Math.max(1, dragTarget.os_height);
      const newW = Math.max(320, dragStartRect.w + (world.x - dragStartMouse.x));
      dragTarget.canvas_width = Math.round(newW);
      dragTarget.canvas_height = Math.round(newW / aspect);
      renderSelectedMonitorInspector();
      render();
    }
  });

  window.addEventListener("mouseup", () => {
    if (dragMode === "move" || dragMode === "resize") {
      activeSnapLines = [];
      scheduleSaveLayout(false);
      render();
    }
    dragMode = null;
    dragTarget = null;
  });

  canvas.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      const worldBefore = screenToWorld(sx, sy);

      const factor = ev.deltaY < 0 ? 1.1 : 0.9;
      view.scale = Math.max(0.06, Math.min(0.8, view.scale * factor));
      view.offsetX = sx - worldBefore.x * view.scale;
      view.offsetY = sy - worldBefore.y * view.scale;
      document.getElementById("zoomLabel").textContent = `${Math.round(
        view.scale * 400
      )}%`;
      render();
    },
    { passive: false }
  );

  // -------------------------------------------------------------------------
  // UI Controls & Inspector Sync
  // -------------------------------------------------------------------------
  function syncUIFromState() {
    renderDeviceList();
    renderSelectedMonitorInspector();

    const s = state.settings;
    document.getElementById("chkSwapCmdCtrl").checked = !!s.swap_cmd_ctrl;
    document.getElementById("chkSwapOptWin").checked = !!s.swap_opt_win;
    document.getElementById("chkClipboardSync").checked = !!s.clipboard_sync;
    document.getElementById("chkInvertScroll").checked = !!s.invert_scroll_on_windows;

    document.getElementById("rngMouseSpeed").value = s.mouse_speed_windows || 1.0;
    document.getElementById("lblMouseSpeed").textContent = `${Number(
      s.mouse_speed_windows || 1.0
    ).toFixed(2)}×`;

    document.getElementById("rngScrollSpeed").value = s.scroll_speed || 1.0;
    document.getElementById("lblScrollSpeed").textContent = `${Number(
      s.scroll_speed || 1.0
    ).toFixed(2)}×`;
  }

  function renderDeviceList() {
    const container = document.getElementById("deviceList");
    container.innerHTML = "";

    for (const dev of state.devices) {
      const div = document.createElement("div");
      div.className = `device-item ${dev.os_type || ""}`;
      const isOnline = !!dev.online;
      const isPlaceholder = !!dev.placeholder;
      const statusText = isOnline
        ? "Online"
        : isPlaceholder
        ? "Preview (Not Paired Yet)"
        : "Offline";
      const ipStr =
        dev.ips && dev.ips.length ? dev.ips.join(", ") : "Click 'Connect Mac & Windows' above";

      div.innerHTML = `
        <div class="device-top">
          <span>${dev.os_type === "darwin" ? "" : "⊞"} ${dev.device_name}</span>
          <span class="status-tag ${isOnline ? "online" : "offline"}">${statusText}</span>
        </div>
        <div class="device-meta">${ipStr}</div>
      `;
      container.appendChild(div);
    }
  }

  function renderSelectedMonitorInspector() {
    const container = document.getElementById("selectedMonitorContent");
    const d = state.displays.find((item) => item.id === selectedId);
    if (!d) {
      container.innerHTML = `<p class="muted">Click any display on the canvas to calibrate its position or physical size.</p>`;
      return;
    }

    const scalePct = Math.round((d.canvas_width / Math.max(1, d.os_width)) * 100);
    container.innerHTML = `
      <div>
        <strong>${d.os_type === "darwin" ? "" : "⊞"} ${d.name}</strong>
        <div class="muted">Native OS Resolution: ${Math.round(d.os_width)} × ${Math.round(d.os_height)} (${d.scale_factor || 1}x scale)</div>
      </div>
      <div class="prop-grid">
        <div class="prop-box">
          <label>Canvas X</label>
          <input type="number" id="inpCanvasX" value="${Math.round(d.canvas_x)}" />
        </div>
        <div class="prop-box">
          <label>Canvas Y</label>
          <input type="number" id="inpCanvasY" value="${Math.round(d.canvas_y)}" />
        </div>
      </div>
      <div class="slider-group">
        <div class="slider-header">
          <span>Physical Monitor Scale</span>
          <strong id="lblDispScale">${scalePct}%</strong>
        </div>
        <input type="range" id="rngDispScale" min="40" max="200" step="2" value="${scalePct}" />
        <small class="muted">Adjust if your MacBook screen is physically smaller or larger than your Windows monitor on your desk.</small>
      </div>
    `;

    document.getElementById("inpCanvasX").addEventListener("change", (ev) => {
      d.canvas_x = Number(ev.target.value) || 0;
      scheduleSaveLayout(false);
      render();
    });

    document.getElementById("inpCanvasY").addEventListener("change", (ev) => {
      d.canvas_y = Number(ev.target.value) || 0;
      scheduleSaveLayout(false);
      render();
    });

    document.getElementById("rngDispScale").addEventListener("input", (ev) => {
      const pct = Number(ev.target.value) / 100;
      d.canvas_width = Math.round(d.os_width * pct);
      d.canvas_height = Math.round(d.os_height * pct);
      document.getElementById("lblDispScale").textContent = `${Math.round(pct * 100)}%`;
      scheduleSaveLayout(false);
      render();
    });
  }

  function updateLiveCursorReadout() {
    const c = state.cursor;
    if (!c) return;
    const disp = state.displays.find((d) => d.id === c.display_id);
    const label = disp ? disp.name : c.device_id;
    document.getElementById(
      "liveCursorReadout"
    ).textContent = `Cursor: ${label} (${c.os_x}, ${c.os_y})`;
  }

  // -------------------------------------------------------------------------
  // Quick Alignment Presets
  // -------------------------------------------------------------------------
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.getAttribute("data-preset");
      const macDisp = state.displays.find((d) => d.os_type === "darwin");
      const winDisp = state.displays.find((d) => d.os_type === "windows");
      if (!macDisp || !winDisp) return;

      if (preset === "mac-left") {
        macDisp.canvas_x = 0;
        macDisp.canvas_y = Math.round((winDisp.canvas_height - macDisp.canvas_height) / 2);
        winDisp.canvas_x = macDisp.canvas_width;
        winDisp.canvas_y = 0;
      } else if (preset === "mac-right") {
        winDisp.canvas_x = 0;
        winDisp.canvas_y = 0;
        macDisp.canvas_x = winDisp.canvas_width;
        macDisp.canvas_y = Math.round((winDisp.canvas_height - macDisp.canvas_height) / 2);
      } else if (preset === "mac-bottom") {
        winDisp.canvas_x = 0;
        winDisp.canvas_y = 0;
        macDisp.canvas_x = Math.round((winDisp.canvas_width - macDisp.canvas_width) / 2);
        macDisp.canvas_y = winDisp.canvas_height;
      } else if (preset === "align-centers") {
        const winCenterY = winDisp.canvas_y + winDisp.canvas_height / 2;
        macDisp.canvas_y = Math.round(winCenterY - macDisp.canvas_height / 2);
      } else if (preset === "align-bottoms") {
        const winBottom = winDisp.canvas_y + winDisp.canvas_height;
        macDisp.canvas_y = Math.round(winBottom - macDisp.canvas_height);
      } else if (preset === "reset-1to1") {
        for (const d of state.displays) {
          d.canvas_width = d.os_width;
          d.canvas_height = d.os_height;
        }
      }

      centerCanvasView();
      renderSelectedMonitorInspector();
      render();
      saveLayoutNow(true);
    });
  });

  // -------------------------------------------------------------------------
  // Settings Event Listeners
  // -------------------------------------------------------------------------
  function onSettingsChange() {
    state.settings.swap_cmd_ctrl = document.getElementById("chkSwapCmdCtrl").checked;
    state.settings.swap_opt_win = document.getElementById("chkSwapOptWin").checked;
    state.settings.clipboard_sync = document.getElementById("chkClipboardSync").checked;
    state.settings.invert_scroll_on_windows = document.getElementById("chkInvertScroll").checked;
    state.settings.mouse_speed_windows = Number(document.getElementById("rngMouseSpeed").value);
    state.settings.scroll_speed = Number(document.getElementById("rngScrollSpeed").value);
    state.settings.corner_barrier_px = 0.0;

    document.getElementById(
      "lblMouseSpeed"
    ).textContent = `${state.settings.mouse_speed_windows.toFixed(2)}×`;
    document.getElementById(
      "lblScrollSpeed"
    ).textContent = `${state.settings.scroll_speed.toFixed(2)}×`;

    render();
    scheduleSaveLayout(false);
  }

  [
    "chkSwapCmdCtrl",
    "chkSwapOptWin",
    "chkClipboardSync",
    "chkInvertScroll",
    "rngMouseSpeed",
    "rngScrollSpeed",
  ].forEach((id) => {
    document.getElementById(id).addEventListener("input", onSettingsChange);
  });

  // Toolbar & Action Buttons
  document.getElementById("btnSaveLayout").addEventListener("click", () => saveLayoutNow(true));
  document.getElementById("btnZoomIn").addEventListener("click", () => {
    view.scale = Math.min(0.8, view.scale * 1.18);
    document.getElementById("zoomLabel").textContent = `${Math.round(view.scale * 400)}%`;
    render();
  });
  document.getElementById("btnZoomOut").addEventListener("click", () => {
    view.scale = Math.max(0.06, view.scale / 1.18);
    document.getElementById("zoomLabel").textContent = `${Math.round(view.scale * 400)}%`;
    render();
  });
  document.getElementById("btnResetView").addEventListener("click", () => {
    centerCanvasView();
    render();
  });

  document.getElementById("btnSimulateMode").addEventListener("click", (ev) => {
    simulateMode = !simulateMode;
    ev.currentTarget.classList.toggle("active", simulateMode);
    if (!simulateMode) simCursor = null;
    render();
  });

  document.getElementById("btnForceMac").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "force_return_mac" }));
      showToast("Sent Force Return to Mac signal!");
    }
  });

  document.getElementById("btnRefreshDisplays").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "refresh_displays" }));
      showToast("Requested display refresh from connected machines");
    }
  });

  // Connect Modal
  const modal = document.getElementById("connectModal");
  function updateModalInstallCommands(netMode) {
    const host =
      netMode === "tailscale"
        ? state.server_info.tailscale_ip || "100.64.0.10"
        : state.server_info.lan_ip || "192.168.1.100";
    const port = state.server_info.http_port || 8765;
    document.getElementById(
      "cmdMacInstall"
    ).textContent = `curl -fsSL http://${host}:${port}/install.sh | bash`;
    document.getElementById(
      "cmdWinInstall"
    ).textContent = `irm http://${host}:${port}/install.ps1 | iex`;
  }

  document.getElementById("btnOpenConnectModal").addEventListener("click", () => {
    updateModalInstallCommands("lan");
    modal.classList.remove("hidden");
  });
  document.getElementById("btnCloseModal").addEventListener("click", () => {
    modal.classList.add("hidden");
  });
  document.getElementById("btnDoneModal").addEventListener("click", () => {
    modal.classList.add("hidden");
  });

  document.getElementById("btnNetLan").addEventListener("click", (ev) => {
    document.getElementById("btnNetLan").classList.add("active");
    document.getElementById("btnNetTailscale").classList.remove("active");
    updateModalInstallCommands("lan");
  });
  document.getElementById("btnNetTailscale").addEventListener("click", (ev) => {
    document.getElementById("btnNetTailscale").classList.add("active");
    document.getElementById("btnNetLan").classList.remove("active");
    updateModalInstallCommands("tailscale");
  });

  document.querySelectorAll(".btn-copy").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-copy-target");
      const text = document.getElementById(targetId).textContent;
      navigator.clipboard.writeText(text).then(() => {
        const prev = btn.textContent;
        btn.textContent = "✓ Copied!";
        setTimeout(() => (btn.textContent = prev), 1500);
      });
    });
  });

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();
  connectWebSocket();
})();
