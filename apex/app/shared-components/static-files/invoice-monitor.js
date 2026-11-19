/* global apex */
globalThis.InvoiceMonitor = (() => {
    "use strict";
    const terminal = new Set(["DONE", "REVIEW", "ERROR"]);
    const pdfFormStates = {QUEUED: "processing", RUNNING: "processing", DONE: "reviewing", REVIEW: "reviewing"};
    const states = {QUEUED:"Queued",RUNNING:"Processing",DONE:"Complete",ERROR:"Error",REVIEW:"Needs review",
                    PENDING:"Pending",found:"Found",matched:"Found",valid:"Valid",ambiguous:"Ambiguous",absent:"Missing",invalid:"Invalid",
                    text:"Text",ocr:"OCR",native:"PDF text"};
    const node = (tag, text) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        return el;
    };
    const named = state => states[state] || state || "—";

    function stageStatus(index, current, state) {
        if (index > current) return "Pending";
        if (state === "ERROR") {
            if (index === current) return "Error";
            return index === 0 ? "Complete" : "Unconfirmed";
        }
        return index < current || terminal.has(state) ? "Complete" : "In progress";
    }

    function timeline(container, titles, current, state) {
        const progress = JSON.stringify([titles, current, state]);
        if (container.dataset.progress === progress) return;
        container.dataset.progress = progress;
        const steps = titles.map((title, index) => {
            const step = node("li");
            const phase = stageStatus(index, current, state);
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

    function reviewControls(root, pages = []) {
        const toolbar = root.querySelector(".inv-page-controls");
        const selector = toolbar.querySelector(".inv-page-choice");
        const layer = toolbar.querySelector(".inv-layer-choice");
        selector.replaceChildren(...(pages.length ? pages.map(p => new Option("Page " + p.PAGE_NO, p.PAGE_NO)) : [new Option("Original PDF")]));
        layer.replaceChildren(...(pages.length ? [new Option("Template fields", "fields"), new Option("Recognized text", "ocr")] : [new Option("No overlay")]));
        selector.disabled = layer.disabled = !pages.length;
        selector.onchange = layer.onchange = null;
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
        const header = node("div"); header.className = "inv-match-header";
        const heading = node("h3");
        const title = node(hasBox ? "button" : "span", (index + 1) + ". " + (field.LABEL || field.FIELD_KEY));
        if (hasBox) {
            title.type = "button"; title.setAttribute("aria-pressed", "false");
            title.setAttribute("aria-label", field.LABEL + ", show evidence on page " + field.PAGE_NO);
            title.addEventListener("click", onClick);
        }
        heading.append(title); header.append(heading); card.append(header);
        if (field.STATUS) {
            const tag = node("span", named(field.STATUS));
            tag.className = "inv-field-status";
            tag.dataset.status = field.STATUS;
            header.append(tag);
        }
        const value = field.NORMALIZED_TEXT ?? field.ORIGINAL_TEXT;
        if (value != null && value !== "") card.append(node("strong", value));
        const source = [field.METHOD && named(field.METHOD), field.PAGE_NO && "Page " + field.PAGE_NO].filter(Boolean).join(", ");
        if (source) card.append(node("small", source));
        return {card, button:hasBox ? title : null};
    }

    function fieldCount(fields) {
        const found = fields.filter(field => field.STATUS === "found").length;
        return {text: fields.length + "/" + found, description: fields.length + " fields total, " + found + " found"};
    }

    function resultsHeading(heading, title, count) {
        heading.textContent = title;
        if (!count) return;
        const tally = node("span", " (" + count.text + ")");
        tally.className = "inv-result-count";
        tally.title = count.description;
        heading.append(tally);
    }

    function pageLayer(page, kind, templateFields, lines) {
        if (kind === "fields") return {title:"Extracted fields", fields:templateFields,
            count:fieldCount(templateFields), empty:"No fields were returned."};
        const fields = lines.filter(l => l.PAGE_NO === page.PAGE_NO).map(l => ({
            FIELD_KEY:"line-" + l.FRAGMENT_NO, LABEL:"Text region " + l.FRAGMENT_NO,
            NORMALIZED_TEXT:l.TEXT_VALUE + (Number(l.TEXT_TRUNCATED) ? "… (text truncated)" : ""),
            METHOD:l.METHOD || page.METHOD, PAGE_NO:l.PAGE_NO, EVIDENCE_BOX:l.EVIDENCE_BOX
        }));
        const shown = page.FRAGMENT_SHOWN ?? fields.length;
        const total = page.FRAGMENT_TOTAL ?? shown;
        const count = shown < total ? {text:shown + "/" + total, description:shown + " of " + total + " text regions shown"} :
            {text:shown, description:shown + " text regions on this page"};
        return {title:"Recognized text", fields, count, empty:"No text regions were saved for this page."};
    }

    function filterOptions(select, fields, all) {
        const selected = select.value;
        select.replaceChildren(new Option(all, ""), ...fields.map(field =>
            new Option(field.LABEL || field.FIELD_KEY, field.FIELD_KEY)));
        select.value = fields.some(field => field.FIELD_KEY === selected) ? selected : "";
        select.disabled = !fields.length;
    }

    function fieldFilters(root, fields) {
        const name = root.querySelector(".inv-field-name");
        const status = root.querySelector(".inv-field-filter");
        filterOptions(name, fields, "All fields");
        status.disabled = !fields.some(field => field.STATUS);
        if (status.disabled) status.value = "";
        return {name, status};
    }

    function fieldRows(fields, name = "", status = "") {
        return fields.map((field, index) => ({field, index}))
            .filter(({field}) => (!name || field.FIELD_KEY === name) && (!status || field.STATUS === status));
    }

    function spotlight(mask, marker) {
        mask.classList.toggle("is-active", Boolean(marker));
        if (!marker) return;
        const {left, top, width, height} = marker.style;
        mask.style.setProperty("--spot-x", left);
        mask.style.setProperty("--spot-y", top);
        mask.style.setProperty("--spot-right", "calc(" + left + " + " + width + ")");
        mask.style.setProperty("--spot-bottom", "calc(" + top + " + " + height + ")");
        marker.classList.add("is-spotlit");
    }

    function setSkeletonState(panel, state) {
        const loading = state === "loading";
        panel.dataset.state = state;
        panel.setAttribute("aria-busy", String(loading));
        panel.querySelector(".t-skel-content").inert = loading;
    }

    function waitForPreview(preview, media) {
        const panel = preview.closest(".t-skel");
        setSkeletonState(panel, "loading");
        function finish(error) {
            if (!preview.contains(media)) return;
            if (error) {
                const message = node("p", "The document preview could not be loaded.");
                message.className = "inv-preview-error";
                media.replaceWith(message);
            }
            setSkeletonState(panel, "ready");
        }
        media.addEventListener("load", () => finish(false), {once:true});
        media.addEventListener("error", () => finish(true), {once:true});
    }

    function renderEvidencePage(preview, viewer, page, mask) {
        const existing = viewer.querySelector("img");
        if (existing?.getAttribute("src") === page.IMAGE_URL) {
            viewer.querySelectorAll(".inv-box").forEach(marker => marker.remove());
            return;
        }
        const img = node("img");
        img.alt = "Page " + page.PAGE_NO + " of the analyzed document";
        img.width = Number(page.WIDTH_PX); img.height = Number(page.HEIGHT_PX);
        viewer.replaceChildren(img, mask);
        waitForPreview(preview, img);
        img.src = page.IMAGE_URL;
    }

    function focusEvidenceMarker(preview, marker) {
        if (!preview.inert) {
            marker.focus();
            return;
        }
        const active = document.activeElement;
        preview.querySelector("img")?.addEventListener("load", () => {
            if (document.activeElement === active && marker.isConnected) marker.focus();
        }, {once:true});
    }

    function evidence(root, pages, templateFields, lines) {
        const {selector, layer} = reviewControls(root, pages);
        layer.options[1].disabled = !lines.length;
        const viewer = node("div"); viewer.className = "inv-page-image";
        const mask = node("div"); mask.className = "inv-evidence-mask"; mask.setAttribute("aria-hidden", "true");
        const preview = root.querySelector(".inv-preview");
        preview.replaceChildren(viewer);
        const results = root.querySelector(".inv-results");
        const heading = root.querySelector("#inv-campos");
        const filters = fieldFilters(root, templateFields);
        const filterControls = filters.name.closest(".inv-field-controls");
        const regionFilter = root.querySelector(".inv-region-filter");
        const regionControls = regionFilter.closest(".inv-region-controls");
        const available = new Map(pages.map(page => [String(page.PAGE_NO), page]));
        const links = new Map(), markers = new Map();
        let chosen, hovered, focused, shownPage, fields, regionsPage;

        function paintSpotlight() {
            const active = hovered ?? focused ?? chosen;
            const page = String(active?.PAGE_NO || selector.value);
            if (shownPage !== page) showPage(page);
            viewer.querySelector(".is-spotlit")?.classList.remove("is-spotlit");
            spotlight(mask, markers.get(active?.FIELD_KEY));
        }

        function highlight(field) {
            chosen = field;
            selector.value = String(field.PAGE_NO);
            links.forEach((item, key) => {
                item.card.classList.toggle("is-selected", key === field.FIELD_KEY);
                item.button?.setAttribute("aria-pressed", String(key === field.FIELD_KEY));
            });
            paintSpotlight();
            markers.forEach((mark, key) => mark.setAttribute("aria-pressed", String(key === field.FIELD_KEY)));
        }

        function showPage(pageNo) {
            shownPage = pageNo;
            const page = available.get(pageNo);
            renderEvidencePage(preview, viewer, page, mask);
            markers.clear();
            fields.forEach(({field, index}) => {
                const box = boxFor(field.EVIDENCE_BOX);
                if (!box || String(field.PAGE_NO) !== pageNo) return;
                const key = field.FIELD_KEY;
                const mark = overlay(field, index, box, key === chosen?.FIELD_KEY, () => {
                    highlight(field); links.get(key).button.focus();
                });
                viewer.append(mark); markers.set(key, mark);
            });
        }

        function previewRow(item, field) {
            item.card.onpointerenter = event => {
                if (event.pointerType !== "mouse") return;
                hovered = field; paintSpotlight();
            };
            item.card.onpointerleave = () => { hovered = undefined; paintSpotlight(); };
            item.button.onfocus = () => { focused = field; paintSpotlight(); };
            item.button.onblur = () => { focused = undefined; paintSpotlight(); };
        }

        function renderLayer() {
            const current = pageLayer(available.get(selector.value), layer.value, templateFields, lines);
            const showText = layer.value === "ocr";
            filterControls.hidden = showText;
            regionControls.hidden = !showText;
            if (showText && regionsPage !== selector.value) {
                regionFilter.value = "";
                filterOptions(regionFilter, current.fields, "All text regions");
                regionsPage = selector.value;
            }
            const name = showText ? regionFilter.value : filters.name.value;
            const status = showText ? "" : filters.status.value;
            fields = fieldRows(current.fields, name, status);
            chosen = hovered = focused = shownPage = undefined;
            resultsHeading(heading, current.title, current.count);
            links.clear(); results.replaceChildren();
            fields.forEach(({field, index}) => {
                const hasBox = boxFor(field.EVIDENCE_BOX) && available.has(String(field.PAGE_NO));
                const item = evidenceCard(field, index, hasBox, () => {
                    highlight(field);
                    focusEvidenceMarker(preview, markers.get(field.FIELD_KEY));
                });
                if (item.button) previewRow(item, field);
                links.set(field.FIELD_KEY, item); results.append(item.card);
            });
            if (!fields.length) results.append(node("p", name || status ? "No fields match these filters." : current.empty));
            paintSpotlight();
        }
        selector.onchange = layer.onchange = filters.name.onchange = filters.status.onchange = regionFilter.onchange = renderLayer;
        renderLayer();
    }

    function unlocatedFields(root, result) {
        const results = root.querySelector(".inv-results");
        const fields = result.fields || [];
        resultsHeading(root.querySelector("#inv-campos"), "Extracted fields", fields.length ? fieldCount(fields) : null);
        const filters = fieldFilters(root, fields);
        filters.name.closest(".inv-field-controls").hidden = false;
        root.querySelector(".inv-region-controls").hidden = true;
        filters.name.onchange = filters.status.onchange = () => {
            const {value: name} = filters.name, {value: status} = filters.status;
            const rows = fieldRows(fields, name, status);
            const empty = name || status ? "No fields match these filters." : result.tracking ?
                "No fields were extracted." : "Run an extraction to see the template fields.";
            results.replaceChildren(...(rows.length ?
                rows.map(({field, index}) => evidenceCard(field, index, false).card) : [node("p", empty)]));
        };
        filters.name.onchange();
    }

    function pdfResult(root, result, row) {
        const heading = root.querySelector(".inv-template-heading");
        heading.textContent = result.tracking ? row.TEMPLATE_NAME || "" : "";
        heading.hidden = !heading.textContent;
        const titles = ["Received", row.STAGE === "OCR" ? "Optical recognition" : "Read PDF", "Extract fields", "Locate evidence", "Result"];
        const stages = {QUEUED:0, READ_PDF:1, OCR:1, EXTRACT_FIELDS:2, LOCATE_EVIDENCE:3, DONE:4};
        const step = row.STATE === "ERROR" ? 4 : (stages[row.STAGE] ?? 0);
        timeline(root.querySelector("ol"), titles, result.tracking ? step : -1, row.STATE);
        const loading = Boolean(result.tracking) && !terminal.has(row.STATE);
        const panel = root.querySelector(".inv-results").closest(".t-skel");
        setSkeletonState(panel, loading ? "loading" : result.tracking ? "ready" : "idle");
        const preview = root.querySelector(".inv-preview");
        const previewPanel = preview.closest(".t-skel");
        if (loading) {
            setSkeletonState(previewPanel, "loading");
            return;
        }
        if (result.tracking && row.STATE !== "ERROR" && result.pages?.length) {
            evidence(root, result.pages, result.fields || [], result.lines || []);
            return;
        }
        if (result.tracking && row.STATE !== "ERROR" && result.preview) {
            showPdf(root, preview, result.preview);
        } else if (result.tracking) {
            reviewControls(root);
            preview.replaceChildren(node("p", "No preview available."));
            setSkeletonState(previewPanel, "ready");
        } else {
            setSkeletonState(previewPanel, "idle");
        }
        unlocatedFields(root, result);
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
        frame.title = "Original PDF preview";
        reviewControls(root);
        waitForPreview(location, frame);
        frame.src = url;
        location.replaceChildren(frame);
    }

    function encodePdfPart(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",", 2)[1]);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(blob);
        });
    }

    async function uploadPdf(file) {
        const uploadId = crypto.randomUUID();
        const partSize = 384 * 1024;
        for (let offset = 0; offset < file.size; offset += partSize) {
            const end = Math.min(offset + partSize, file.size);
            const encoded = await encodePdfPart(file.slice(offset, end));
            const response = await apex.server.process("PROBAR", {
                x01: uploadId,
                x02: String(offset / partSize),
                x04: file.name,
                x05: end === file.size ? "Y" : "N",
                f01: apex.server.chunk(encoded)
            }, {dataType: "json", timeout: 90000});
            if (end === file.size || !response?.received) return response;
        }
    }

    const pdfInput = 'input[type=file]#P6_FILE, #P6_FILE_CONTAINER input[type=file]';

    function pdfSubmission(root, setStatus, accepted, reset) {
        const run = document.querySelector(".inv-pdf-run");
        const label = run?.querySelector(".t-Button-label") ?? run;
        const inputRegion = run?.closest(".inv-pdf-input");
        let state = "idle", file;

        function syncRunButton() {
            if (run) run.disabled = state !== "reviewing" && (state === "processing" || !file);
        }

        function setState(next) {
            state = next;
            inputRegion?.classList.toggle("is-processing", state === "processing");
            inputRegion?.classList.toggle("is-reviewing", state === "reviewing");
            if (label) label.textContent = state === "reviewing" ? "New execution" : "Run extraction";
            syncRunButton();
        }

        function uploadFailed(message) {
            setState("idle");
            root.classList.remove("is-visible");
            pdfResult(root, {}, {});
            setStatus(message, true);
        }

        syncRunButton();
        run?.addEventListener("click", async () => {
            if (run.disabled) return;
            if (state === "reviewing") {
                setState("idle");
                reset();
                inputRegion?.querySelector(pdfInput)?.focus();
                return;
            }
            setState("processing");
            root.classList.add("is-visible");
            pdfResult(root, {tracking: true}, {STAGE: "QUEUED", TEMPLATE_NAME: root.dataset.templateName});
            setStatus("Submitting PDF for extraction…");
            let response;
            try {
                response = await uploadPdf(file);
            } catch {
                uploadFailed("The upload could not be confirmed. Reload the page to check its status before retrying.");
                return;
            }
            if (!response?.tracking) {
                uploadFailed(response?.message || "The PDF was not accepted. Choose a PDF file and try again.");
                return;
            }
            accepted(response.tracking);
        });
        return {
            setState,
            setFile(selected) { file = selected; syncRunButton(); }
        };
    }

    function pdfMonitor(root, setStatus) {
        const preview = root.querySelector(".inv-preview");
        let selectedFile, tracking, submission;

        function clearPreview(message) {
            reviewControls(root);
            preview.replaceChildren(node("p", message));
            setSkeletonState(preview.closest(".t-skel"), "idle");
        }

        function selectFile(file) {
            if (file === selectedFile) return;
            selectedFile = file;
            tracking = null;
            root.classList.remove("is-visible");
            submission.setFile(null);
            pdfResult(root, {}, {});
            if (!file) {
                clearPreview("Select a PDF to preview it.");
                return;
            }
            if (!file.size || file.size > 12 * 1024 * 1024 || !/\.pdf$/i.test(file.name)) {
                clearPreview("No preview available.");
                setStatus("Select a PDF file up to 12 MiB.", true);
                return;
            }
            submission.setFile(file);
            setStatus("Ready to extract: " + file.name);
        }

        function reset() {
            tracking = null;
            root.classList.remove("is-visible");
            clearPreview("Select a PDF to preview it.");
            pdfResult(root, {}, {});
            setStatus("Choose a PDF and run extraction.");
        }

        function start(update) {
            submission = pdfSubmission(root, setStatus, id => {
                tracking = id;
                selectedFile = null;
                submission.setFile(null);
                apex.item("P6_FILE").setValue("", null, true);
                update();
            }, reset);
            document.addEventListener("change", event => {
                if (event.target.matches(pdfInput)) {
                    selectFile(event.target.files?.[0]);
                }
            }, true);
            update();
        }

        return {
            start,
            pollInterval: 1000,
            paused:() => tracking === null || Boolean(selectedFile),
            params:() => ({x01: tracking}),
            render(result, row) {
                root.dataset.templateName = result.templateName || "";
                submission.setState(pdfFormStates[row.STATE] || "idle");
                root.classList.toggle("is-visible", Boolean(result.tracking));
                pdfResult(root, result, row);
                const issue = row.ERROR_CODE === "dispatch_failed" ?
                    "The extraction service did not accept the PDF. Run extraction again." : row.ERROR_CODE;
                return [row.FILENAME, row.STATE ? named(row.STATE) : "Queued", issue];
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
            pollInterval: 4000,
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
        function setStatus(text, error = false) {
            status.classList.toggle("is-error", error);
            if (status.textContent !== text) status.textContent = text;
        }
        const isPdf = root.dataset.kind === "pdf";
        const view = isPdf ? pdfMonitor(root, setStatus) : zipMonitor(root, setStatus);
        const refresh = root.querySelector(".inv-refresh");
        let timer, busy = false, failures = 0, pendingPdfRefresh = false;
        async function update(selection) {
            if (view.paused?.()) return;
            if (busy) {
                pendingPdfRefresh = isPdf;
                return;
            }
            busy = true;
            if (refresh) refresh.disabled = true;
            view.lock?.(true); clearTimeout(timer);
            try {
                const result = await apex.server.process("ESTADO", view.params(selection), {dataType:"json"});
                if (view.paused?.() || pendingPdfRefresh) return;
                const row = result.items?.[0] || {};
                const details = view.render(result, row);
                failures = 0;
                const empty = isPdf ? "Choose a PDF and run extraction." :
                    "No upload is selected for this session.";
                setStatus(result.tracking ? details.filter(Boolean).join(", ") : empty, row.STATE === "ERROR");
                if (result.tracking && !terminal.has(row.STATE)) timer = setTimeout(update, view.pollInterval);
            } catch {
                if (isPdf && ++failures <= 3) {
                    setStatus("Status could not be loaded. Retrying…", true);
                    timer = setTimeout(update, 4000);
                } else {
                    const action = isPdf ? "Reload the page" : "Choose Refresh status";
                    setStatus("Status could not be loaded. " + action + " to try again.", true);
                }
            } finally {
                busy = false;
                if (refresh) refresh.disabled = false;
                view.lock?.(false);
                if (pendingPdfRefresh) {
                    pendingPdfRefresh = false;
                    update();
                }
            }
        }
        refresh?.addEventListener("click", () => { view.refresh?.(); update(); });
        window.addEventListener("pagehide", () => clearTimeout(timer), {once:true});
        view.start(update);
    }
    return {start};
})();
