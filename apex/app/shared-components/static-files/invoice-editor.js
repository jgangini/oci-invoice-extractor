/* global apex */
globalThis.InvoiceEditor = (() => {
    "use strict";
    const id = (name) => document.getElementById(name);
    const details = [
        ["id", "Identifier"], ["group", "Group"], ["definition", "Definition"],
        ["exclusions", "Exclusions"], ["normalization", "Normalization"], ["source", "Source"]
    ];
    let draft, modified = false;

    function message(text, error = false) {
        id("inv-message").textContent = text;
        id("inv-message").classList.toggle("inv-error", error);
    }

    function boundedText(value, max) {
        return typeof value === "string" && !!value.trim() && value.length <= max;
    }

    function validFieldKind(field) {
        const formats = {string:["string","year"], date:["date"], currency:["currency"]};
        return Object.hasOwn(formats, field.type) && formats[field.type].includes(field.format || field.type)
            && (!field.currency || (field.type === "currency" && /^[A-Z]{3}$/.test(field.currency)));
    }

    function validField(field, keys) {
        if (!field || !boundedText(field.label, 200) || !boundedText(field.key, 64)) return false;
        if (!/^[a-z][a-z0-9_]*$/.test(field.key) || keys.has(field.key) || !validFieldKind(field)) return false;
        if (!["required", "optional", "conditional"].includes(field.applicability)) return false;
        return Array.isArray(field.labels) && field.labels.length > 0 && field.labels.length <= 25
            && field.labels.every(label => boundedText(label, 100));
    }

    function validate(value) {
        if (!value || !boundedText(value.name, 100) ||
            !Array.isArray(value.fields) ||
            !value.fields.length || value.fields.length > 100) {
            throw Error("Enter a name and 1 to 100 fields.");
        }
        delete value.version;
        const keys = new Set();
        value.fields.forEach((field, i) => {
            if (!validField(field, keys)) {
                throw Error("Check the key, label, matching text and type of field " + (i + 1) + ".");
            }
            keys.add(field.key);
        });
        if (new Blob([JSON.stringify(value)]).size > 250000) throw Error("The template exceeds 250 KB.");
        return value;
    }

    function element(tag, text) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function control(parent, field, key, title, options) {
        const label = element("label", title);
        const input = element(options ? "select" : ["labels", "definition", "exclusions", "normalization", "source"].includes(key) ? "textarea" : "input");
        if (options) options.forEach(([v, title]) => input.add(new Option(title, v)));
        input.value = key === "labels" ? (field.labels || []).join("\n") : field[key] || "";
        input.addEventListener("input", () => {
            const value = input.value;
            if (key === "labels") field.labels = value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
            else if (value) field[key] = value;
            else delete field[key];
            modified = true;
        });
        label.append(input);
        parent.append(label);
        return input;
    }

    function drawFields() {
        const parent = id("inv-fields");
        parent.replaceChildren();
        draft.fields.forEach((field, i) => {
            const panel = element("details");
            panel.className = "inv-field";
            const title = element("summary", (i + 1) + ". " + (field.label || "New field"));
            const form = element("div");
            form.className = "inv-grid";
            panel.append(title, form);
            const caption = control(form, field, "label", "Field label");
            caption.addEventListener("input", () => { title.textContent = (i + 1) + ". " + (caption.value || "New field"); });
            control(form, field, "key", "Field key");
            control(form, field, "type", "Data type", [["string","Text"],["date","Date"],["currency","Amount"]]);
            control(form, field, "applicability", "Applicability", [["required","Required"],["optional","Optional"],["conditional","Conditional"]]);
            control(form, field, "labels", "Matching document labels (one per line)");
            control(form, field, "format", "Format", [["","Use the data type"],["year","Year"],["string","Text"],["date","Date"],["currency","Amount"]]);
            control(form, field, "currency", "ISO currency code (if applicable)");
            details.forEach(([key, label]) => control(form, field, key, label));
            const remove = element("button", "Remove field");
            remove.type = "button";
            remove.addEventListener("click", () => {
                draft.fields.splice(i, 1);
                modified = true;
                drawFields();
                id("inv-add").focus();
            });
            form.append(remove);
            parent.append(panel);
        });
    }

    function show(data) {
        draft = data;
        id("inv-name").value = data.name;
        drawFields();
    }

    function definition() {
        draft.name = id("inv-name").value.trim();
        return validate(draft);
    }

    async function load() {
        const data = await apex.server.process("ABRIR", {}, {dataType:"json"});
        if (!modified && data.definition) show(validate(JSON.parse(data.definition)));
    }

    function act(target, fn) {
        id(target).addEventListener("click", async event => {
            const button = event.currentTarget;
            button.disabled = true;
            try { await fn(); }
            catch (e) { message(e.message || "The request failed. Check your session and try again.", true); }
            finally { button.disabled = false; }
        });
    }

    function allowReplace() {
        return !modified || window.confirm("Replace the unsaved changes to this template?");
    }

    function start() {
        const root = id("invoice-editor");
        show({name:"", fields:[]});
        root.addEventListener("input", () => { modified = true; });
        load().catch(() => message("The template could not be loaded.", true));
        act("inv-import", () => id("inv-json-file").click());
        id("inv-json-file").addEventListener("change", async e => {
            try {
                const file = e.target.files[0];
                if (!file || !allowReplace()) return;
                if (file.size > 250000) throw Error("The JSON file exceeds 250 KB.");
                const data = validate(JSON.parse((await file.text()).replace(/^\uFEFF/, "")));
                show(data); modified = true; message("Template imported. Review its fields before saving.");
            } catch (err) { message(err instanceof SyntaxError ? "The file does not contain valid JSON." : err.message, true); }
            finally { e.target.value = ""; }
        });
        act("inv-add", () => {
            if (draft.fields.length >= 100) throw Error("A template supports up to 100 fields.");
            draft.fields.push({label:"",key:"",type:"string",applicability:"optional",labels:[]});
            modified = true; drawFields();
            const field = id("inv-fields").lastElementChild;
            field.open = true; field.querySelector("input").focus();
        });
        act("inv-download", () => {
            const value = definition();
            const blob = new Blob([JSON.stringify(value, null, 2) + "\n"], {type:"application/json;charset=utf-8"});
            const link = element("a");
            link.download = value.name.replace(/[^\p{L}\p{N}_-]+/gu, "-") + ".json";
            link.href = URL.createObjectURL(blob);
            root.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 2000);
            message("Download ready.");
        });
        act("inv-save", async () => {
            const text = JSON.stringify(definition());
            const chunks = Array.from(text.matchAll(/[\s\S]{1,3000}/gu), m => m[0]);
            await apex.server.process("GUARDAR", {f01:chunks}, {dataType:"json"});
            modified = false;
            message("Template saved.");
        });
    }
    return {start};
})();
