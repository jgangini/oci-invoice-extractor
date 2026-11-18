/* global apex */
globalThis.InvoiceMonitor = (() => {
    "use strict";
    const terminal = new Set(["DONE", "REVIEW", "ERROR"]);
    const states = {QUEUED:"Queued",RUNNING:"Processing",DONE:"Complete",ERROR:"Error",REVIEW:"Needs review",
                    PENDING:"Pending",found:"Found",matched:"Found",valid:"Valid",ambiguous:"Ambiguous",absent:"Missing",invalid:"Invalid",
                    text:"Text",ocr:"OCR",native:"PDF text"};
    const node = (tag, text) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        return el;
    };
    const named = state => states[state] || state || "—";

    function timeline(container, titles, current, state) {
        const steps = titles.map((title, index) => {
            const step = node("li");
            const phase = current < 0 || index > current ? "Pending" :
                index === current && state === "ERROR" ? "Error" :
                state === "ERROR" && index > 0 ? "Unconfirmed" :
                index < current || state === "DONE" || state === "REVIEW" ? "Complete" : "In progress";
            step.dataset.state = phase;
            if (index === current) step.setAttribute("aria-current", "step");
            step.append(node("strong", title), node("small", phase));
            return step;
        });
        container.replaceChildren(...steps);
    }

    function report(container, headings, rows, empty) {
        if (!rows.length) { container.replaceChildren(node("p", empty)); return; }
        const table = node("table");
        const header = table.createTHead().insertRow();
        headings.forEach(title => {
            const cell = node("th", title); cell.scope = "col"; header.append(cell);
        });
        const body = table.createTBody();
        rows.forEach(values => {
            const row = body.insertRow();
            values.forEach((value, i) => {
                const cell = row.insertCell();
                cell.dataset.label = headings[i]; cell.textContent = value ?? "—";
            });
        });
        container.replaceChildren(table);
    }

    function metric(parent, title, value) {
        const box = node("div");
        box.append(node("small", title), node("strong", value));
        parent.append(box);
    }

    function boxFor(value) {
        try {
            const b = typeof value === "string" ? JSON.parse(value) : value;
            return Array.isArray(b) && b.length === 4 && b.every(v => Number.isFinite(v) && v >= 0 && v <= 1) &&
                b[0] < b[2] && b[1] < b[3] ? b : null;
        } catch { return null; }
    }

    function reviewControls(root, url, pages = []) {
        const toolbar = root.querySelector(".inv-page-controls");
        const selector = toolbar.querySelector(".inv-page-choice");
        const layer = toolbar.querySelector(".inv-layer-choice");
        const open = toolbar.querySelector(".inv-open-original");
        selector.replaceChildren(...(pages.length ? pages.map(p => new Option("Page " + p.PAGE_NO, p.PAGE_NO)) : [new Option("Original PDF")]));
        layer.replaceChildren(...(pages.length ? [new Option("Template fields", "fields"), new Option("Recognized text", "ocr")] : [new Option("No overlay")]));
        selector.disabled = layer.disabled = !pages.length;
        selector.onchange = layer.onchange = null;
        open.disabled = !url;
        open.onclick = url ? () => window.open(url, "_blank", "noopener,noreferrer") : null;
        return {selector, layer};
    }

    function overlay(field, index, box, selected, onClick) {
        const mark = node("button");
        mark.type = "button"; mark.className = "inv-box inv-ink-" + index % 6;
        mark.dataset.key = field.FIELD_KEY; mark.title = field.LABEL;
        mark.setAttribute("aria-label", "Show field " + (index + 1) + ", " + field.LABEL);
        mark.setAttribute("aria-pressed", String(selected));
        [mark.style.left, mark.style.top, mark.style.width, mark.style.height] =
            [box[0], box[1], box[2] - box[0], box[3] - box[1]].map(n => (100 * n) + "%");
        mark.append(node("span", String(index + 1)), node("b", field.LABEL || field.FIELD_KEY));
        mark.addEventListener("click", onClick);
        return mark;
    }

    function evidenceCard(field, index, hasBox, onClick) {
        const card = node("article"); card.className = "inv-match inv-ink-" + index % 6;
        const heading = node("h3");
        const title = node(hasBox ? "button" : "span", (index + 1) + ". " + (field.LABEL || field.FIELD_KEY));
        if (hasBox) {
            title.type = "button"; title.setAttribute("aria-pressed", "false");
            title.setAttribute("aria-label", field.LABEL + ", show evidence on page " + field.PAGE_NO);
            title.addEventListener("click", onClick);
        }
        heading.append(title); card.append(heading, node("strong", field.NORMALIZED_TEXT ?? field.ORIGINAL_TEXT ?? "No value"));
        const source = hasBox ? "Page " + field.PAGE_NO : "No location available";
        card.append(node("small", [named(field.STATUS), field.METHOD && named(field.METHOD), source].filter(Boolean).join(", ")));
        const anchor = field.ANCHOR_TEXT || field.EVIDENCE;
        if (anchor) card.append(node("p", anchor));
        return {card, button:hasBox ? title : null};
    }

    function pageLayer(page, kind, templateFields, lines) {
        if (kind === "fields") return {title:"Extracted fields", fields:templateFields, summary:"", empty:"No fields were returned."};
        const fields = lines.filter(l => l.PAGE_NO === page.PAGE_NO).map(l => ({
            FIELD_KEY:"line-" + l.FRAGMENT_NO, LABEL:"Text region " + l.FRAGMENT_NO,
            NORMALIZED_TEXT:l.TEXT_VALUE + (Number(l.TEXT_TRUNCATED) ? "… (text truncated)" : ""),
            METHOD:l.METHOD || page.METHOD, PAGE_NO:l.PAGE_NO, EVIDENCE_BOX:l.EVIDENCE_BOX
        }));
        const count = "Text regions: " + (page.FRAGMENT_SHOWN ?? fields.length) + " of " + (page.FRAGMENT_TOTAL ?? fields.length);
        const summary = count + (Number(page.FRAGMENT_TRUNCATED) ? ". The preview limit was reached." : ".");
        return {title:"Recognized text", fields, summary, empty:"No text regions were saved for this page."};
    }

    function evidence(root, pages, templateFields, originalUrl, lines) {
        const {selector, layer} = reviewControls(root, originalUrl, pages);
        layer.options[1].disabled = !lines.length;
        const viewer = node("div"); viewer.className = "inv-page-image";
        const limit = node("p"); limit.className = "inv-ocr-summary"; limit.setAttribute("role", "status");
        root.querySelector(".inv-preview").replaceChildren(limit, viewer);
        const results = root.querySelector(".inv-results");
        const available = new Map(pages.map(page => [String(page.PAGE_NO), page]));
        const links = new Map(), markers = new Map();
        let chosen, fields;

        function highlight(field) {
            chosen = field.FIELD_KEY;
            links.forEach((item, key) => {
                item.card.classList.toggle("is-selected", key === chosen);
                item.button?.setAttribute("aria-pressed", String(key === chosen));
            });
            markers.forEach((mark, key) => mark.setAttribute("aria-pressed", String(key === chosen)));
        }

        function showPage() {
            const page = available.get(selector.value);
            const img = node("img"); img.src = page.IMAGE_URL;
            img.alt = "Page " + page.PAGE_NO + " of the analyzed document";
            img.width = Number(page.WIDTH_PX); img.height = Number(page.HEIGHT_PX);
            viewer.replaceChildren(img); markers.clear();
            fields.forEach((field, index) => {
                const box = boxFor(field.EVIDENCE_BOX);
                if (!box || String(field.PAGE_NO) !== selector.value) return;
                const key = field.FIELD_KEY;
                const mark = overlay(field, index, box, key === chosen, () => {
                    highlight(field); links.get(key).button.focus();
                });
                viewer.append(mark); markers.set(key, mark);
            });
        }

        function renderLayer() {
            const current = pageLayer(available.get(selector.value), layer.value, templateFields, lines);
            fields = current.fields; chosen = undefined;
            limit.textContent = current.summary;
            results.previousElementSibling.textContent = current.title;
            links.clear(); results.replaceChildren();
            fields.forEach((field, index) => {
                const hasBox = boxFor(field.EVIDENCE_BOX) && available.has(String(field.PAGE_NO));
                const item = evidenceCard(field, index, hasBox, () => {
                    selector.value = String(field.PAGE_NO); highlight(field); showPage();
                    markers.get(field.FIELD_KEY).focus();
                });
                links.set(field.FIELD_KEY, item); results.append(item.card);
            });
            if (!fields.length) results.append(node("p", current.empty));
            showPage();
        }
        selector.onchange = layer.onchange = renderLayer;
        renderLayer();
    }

    function pdfResult(root, result, row) {
        const titles = ["Received", row.STAGE === "OCR" ? "Optical recognition" : "Read PDF", "Extract fields", "Locate evidence", "Result"];
        const stages = {QUEUED:0, READ_PDF:1, OCR:1, EXTRACT_FIELDS:2, LOCATE_EVIDENCE:3, DONE:4};
        const step = row.STATE === "ERROR" ? 4 : (stages[row.STAGE] ?? 0);
        timeline(root.querySelector("ol"), titles, result.tracking ? step : -1, row.STATE);
        if (result.pages?.length && terminal.has(row.STATE)) {
            evidence(root, result.pages, result.fields || [], result.preview, result.lines || []);
            return;
        }
        report(root.querySelector(".inv-results"), ["Field", "Value", "Status", "Evidence"],
            (result.fields || []).map(f => [f.LABEL || f.FIELD_KEY, f.NORMALIZED_TEXT ?? f.ORIGINAL_TEXT,
                named(f.STATUS), [f.PAGE_NO && "Page " + f.PAGE_NO, f.METHOD && named(f.METHOD), f.EVIDENCE].filter(Boolean).join(", ") || "No location available"]),
            result.tracking ? "Results will appear when extraction finishes." : "Run an extraction to see the template fields.");
    }

    function zipResult(root, result, row) {
        const done = terminal.has(row.STATE);
        const step = done ? 3 : row.BATCH_ID ? 2 : 1;
        timeline(root.querySelector("ol"), ["Upload to bucket", "Receive event", "Process documents", "Result"],
                 result.tracking ? step : -1, row.STATE);
        const area = root.querySelector(".inv-metrics");
        area.replaceChildren();
        metric(area, "Processed", row.PROCESSED_DOCS ?? "—");
        metric(area, "Remaining", row.REMAINING_DOCS ?? "—");
        metric(area, "Errors", row.ERROR_DOCS ?? "—");
        const seconds = row.ETA_SECONDS;
        const eta = seconds == null ? "Awaiting data" : seconds === 0 ? "0 min" :
            seconds < 60 ? "Less than 1 min" : Math.ceil(seconds / 60) + " min";
        metric(area, "Estimated time remaining", eta);
        report(root.querySelector(".inv-results"), ["Document", "Status", "Error"],
            (result.documents || []).map(d => [d.MEMBER_NAME, named(d.STATE), d.ERROR_CODE]),
            result.tracking ? "Documents will appear as they are processed." : "Upload or select a batch to see its documents.");
    }

    function showPdf(root, location, url) {
        const frame = node("iframe");
        frame.title = "Original PDF preview"; frame.src = url;
        reviewControls(root, url);
        location.replaceChildren(frame);
    }

    function pdfMonitor(root, setStatus) {
        const preview = root.querySelector(".inv-preview");
        let objectUrl, previewId, selected = false;

        function fileChanged(event) {
            if (!event.target.matches("#P6_FILE, #P6_FILE_CONTAINER input[type=file]")) return;
            const file = event.target.files?.[0];
            if (!file) return;
            selected = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            if (file.size > 12 * 1024 * 1024 || !/\.pdf$/i.test(file.name)) {
                reviewControls(root, null);
                pdfResult(root, {}, {});
                setStatus("Select a PDF file up to 12 MiB.");
                preview.replaceChildren(node("p", "No preview available.")); return;
            }
            objectUrl = URL.createObjectURL(new Blob([file], {type:"application/pdf"}));
            showPdf(root, preview, objectUrl); pdfResult(root, {}, {});
            setStatus("Document selected. Choose Run extraction to start.");
        }

        document.addEventListener("change", fileChanged);
        window.addEventListener("pagehide", () => { if (objectUrl) URL.revokeObjectURL(objectUrl); }, {once:true});
        return {
            start:update => update(),
            paused:() => selected,
            params:() => ({}),
            render(result, row) {
                pdfResult(root, result, row);
                const hasEvidence = result.pages?.length && terminal.has(row.STATE);
                if (result.preview && !hasEvidence && previewId !== result.tracking) {
                    previewId = result.tracking; showPdf(root, preview, result.preview);
                }
                return [row.FILENAME, row.STATE ? named(row.STATE) : "Queued", row.ERROR_CODE];
            }
        };
    }

    function zipMonitor(root, setStatus) {
        const batches = root.querySelector(".inv-batch");
        const filter = root.querySelector(".inv-filter");
        const prev = root.querySelector(".inv-prev"), next = root.querySelector(".inv-next");
        let offset = 0;

        async function recent() {
            const previous = batches.selectedOptions[0];
            const data = await apex.server.process("RECIENTES", {x01:filter.value}, {dataType:"json"});
            batches.replaceChildren(new Option("Latest upload in this session", "self"));
            for (const b of data.batches) batches.add(new Option("#" + b.BATCH_ID + ", " + b.OBJECT_KEY, b.BATCH_ID));
            if (previous?.value && !Array.from(batches.options).some(o => o.value === previous.value)) batches.add(previous);
            batches.value = previous?.value || "self";
        }
        const search = () => recent().catch(() => setStatus("Batches could not be loaded."));

        function render(result, row) {
            zipResult(root, result, row);
            const count = result.documentCount || 0;
            root.querySelector(".inv-range").textContent = count ? (offset + 1) + "–" + Math.min(offset + 20, count) + " of " + count : "0 documents";
            prev.disabled = offset === 0; next.disabled = offset + 20 >= count;
            if (row.BATCH_ID) {
                const key = String(row.BATCH_ID);
                if (!Array.from(batches.options).some(o => o.value === key)) batches.add(new Option("#" + key + ", " + result.tracking, key));
                batches.value = key;
            }
            return [result.tracking, row.STATE ? named(row.STATE) : "Uploaded to the bucket. Waiting for the event.", row.ERROR_CODE];
        }
        return {
            render, refresh:search,
            params:selection => ({x01:selection, x02:String(offset)}),
            lock(busy) { batches.disabled = busy; if (busy) prev.disabled = next.disabled = true; },
            start(update) {
                root.querySelector(".inv-search").addEventListener("click", search);
                filter.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); search(); } });
                batches.addEventListener("change", () => { offset = 0; update(batches.value); });
                prev.addEventListener("click", () => { offset = Math.max(0, offset - 20); update(); });
                next.addEventListener("click", () => { offset += 20; update(); });
                search().finally(() => update());
            }
        };
    }

    function start() {
        const root = document.getElementById("invoice-monitor");
        const status = root.querySelector(".inv-status");
        const setStatus = text => { if (status.textContent !== text) status.textContent = text; };
        const view = root.dataset.kind === "pdf" ? pdfMonitor(root, setStatus) : zipMonitor(root, setStatus);
        const refresh = root.querySelector(".inv-refresh");
        let timer;
        async function update(selection) {
            if (refresh.disabled || view.paused?.()) return;
            refresh.disabled = true; view.lock?.(true); clearTimeout(timer);
            try {
                const result = await apex.server.process("ESTADO", view.params(selection), {dataType:"json"});
                if (view.paused?.()) return;
                const row = result.items?.[0] || {};
                const details = view.render(result, row);
                setStatus(result.tracking ? details.filter(Boolean).join(", ") : "No upload is selected for this session.");
                if (result.tracking && !terminal.has(row.STATE)) timer = setTimeout(update, 4000);
            } catch {
                setStatus("Status could not be loaded. Choose Refresh status to try again.");
            } finally {
                refresh.disabled = false; view.lock?.(false);
            }
        }
        refresh.addEventListener("click", () => { view.refresh?.(); update(); });
        window.addEventListener("pagehide", () => clearTimeout(timer), {once:true});
        view.start(update);
    }
    return {start};
})();
