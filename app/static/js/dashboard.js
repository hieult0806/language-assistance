(() => {
    const drawTrendChart = () => {
        const canvas = document.getElementById("trend-chart");
        if (!canvas) {
            return;
        }

        const raw = canvas.dataset.series;
        let points = [];
        try {
            points = JSON.parse(raw || "[]");
        } catch {
            points = [];
        }

        const width = canvas.clientWidth || 640;
        const height = canvas.clientHeight || 240;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = width * dpr;
        canvas.height = height * dpr;

        const ctx = canvas.getContext("2d");
        if (!ctx || points.length === 0) {
            return;
        }
        ctx.scale(dpr, dpr);

        const padding = { top: 20, right: 16, bottom: 28, left: 16 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        const grammarValues = points.map((item) => Number(item.avg_grammar_score || 0));
        const clarityValues = points.map((item) => Number(item.avg_clarity_score || 0));
        const allValues = [...grammarValues, ...clarityValues];
        const minValue = Math.max(0, Math.min(...allValues, 50) - 10);
        const maxValue = Math.min(100, Math.max(...allValues, 90) + 5);

        const xFor = (index) =>
            padding.left + (chartWidth * index) / Math.max(points.length - 1, 1);
        const yFor = (value) =>
            padding.top + chartHeight - ((value - minValue) / Math.max(maxValue - minValue, 1)) * chartHeight;

        ctx.clearRect(0, 0, width, height);
        ctx.strokeStyle = "rgba(33, 24, 20, 0.12)";
        ctx.lineWidth = 1;
        for (let step = 0; step <= 4; step += 1) {
            const value = minValue + ((maxValue - minValue) * step) / 4;
            const y = yFor(value);
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(width - padding.right, y);
            ctx.stroke();
        }

        const drawSeries = (values, color) => {
            ctx.beginPath();
            values.forEach((value, index) => {
                const x = xFor(index);
                const y = yFor(value);
                if (index === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            });
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.stroke();

            values.forEach((value, index) => {
                ctx.beginPath();
                ctx.arc(xFor(index), yFor(value), 3.5, 0, Math.PI * 2);
                ctx.fillStyle = color;
                ctx.fill();
            });
        };

        drawSeries(grammarValues, "#0b6e4f");
        drawSeries(clarityValues, "#c15f3c");

        ctx.fillStyle = "rgba(33, 24, 20, 0.7)";
        ctx.font = '12px "Aptos", "Segoe UI", sans-serif';
        points.forEach((point, index) => {
            const x = xFor(index);
            const label = String(point.day || "").slice(5);
            ctx.fillText(label, x - 14, height - 8);
        });
    };

    const startPromptHistoryRefresh = () => {
        const panel = document.getElementById("prompt-history-panel");
        if (!panel) {
            return;
        }

        const refreshUrl = panel.dataset.refreshUrl;
        const refreshIntervalMs = Number(panel.dataset.refreshIntervalMs || 3000);
        if (!refreshUrl || refreshIntervalMs < 1000) {
            return;
        }

        let activeRequest = null;
        let lastMarkup = panel.innerHTML;

        const refresh = async () => {
            if (document.hidden || activeRequest) {
                return;
            }

            try {
                activeRequest = fetch(refreshUrl, {
                    credentials: "same-origin",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                    },
                });
                const response = await activeRequest;
                if (response.redirected) {
                    window.location.assign(response.url);
                    return;
                }
                if (!response.ok) {
                    return;
                }

                const markup = await response.text();
                if (markup && markup !== lastMarkup) {
                    panel.innerHTML = markup;
                    lastMarkup = markup;
                }
            } catch {
                // Keep polling even if one refresh attempt fails.
            } finally {
                activeRequest = null;
            }
        };

        window.setInterval(refresh, refreshIntervalMs);
    };

    drawTrendChart();
    startPromptHistoryRefresh();
})();
