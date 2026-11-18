"""Run the shipped APEX markup and scripts with deterministic AJAX responses in Chromium."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apex/app"
FILES = APP / "shared-components/static-files"
BROWSER = shutil.which("chromium") or shutil.which("google-chrome") or next(
    (str(p) for var in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(var)
     for p in [Path(os.environ[var]) / "Microsoft/Edge/Application/msedge.exe"] if p.is_file()), None)


@unittest.skipUnless(BROWSER, "A local Chromium browser is required for the APEX controls test")
class ApexControlsTest(unittest.TestCase):
    def test_template_round_trip_and_external_batch_pagination(self):
        harness = """
        const check = (test, text) => { if (!test) throw Error(text); };
        const nextTick = () => new Promise(r => setTimeout(r, 10));
        const editor = document.getElementById("invoice-editor");
        const zip = document.getElementById("invoice-monitor");
        const dom = selector => document.querySelector(selector);
        let saved, downloaded, selected, pages = [];
        window.apex = {server:{process:async (name, data) => {
          if (name === "VERSIONES") return {versions:[{ID:1,NAME:"Original",VERSION:1}]};
          if (name === "GUARDAR") { saved=JSON.parse(data.f01.join("")); return {id:1}; }
          if (name === "RECIENTES") return {batches:[{BATCH_ID:27,OBJECT_KEY:"entrada/externo.zip"}]};
          if (name === "ESTADO") {
            if (data.x01) selected=data.x01;
            if (!selected) return {};
            const offset=Number(data.x02); pages.push(offset);
            return {tracking:"entrada/externo.zip",items:[{BATCH_ID:27,STATE:"DONE",PROCESSED_DOCS:21,ETA_SECONDS:0}],
                    documentCount:21,documents:[{MEMBER_NAME:"documento-"+offset,STATE:"DONE"}]};
          }
          throw Error(name);
        }}};
        HTMLAnchorElement.prototype.click=function() {downloaded=this.href;};
        (async()=>{
          InvoiceEditor.start(); InvoiceMonitor.start(); await nextTick();
          const transfer=new DataTransfer();
          transfer.items.add(new File([JSON.stringify(catalog)],"plantilla.json",{type:"application/json"}));
          const input=dom("#inv-json-file"); input.files=transfer.files; input.dispatchEvent(new Event("change"));
          for(let i=0; i<100 && !editor.querySelector("details"); i++) await nextTick();
          check(editor.querySelectorAll("details").length===12,"import");
          const label=editor.querySelector("details input");
          label.value="Campo editado"; label.dispatchEvent(new Event("input",{bubbles:true}));
          dom("#inv-number").value="2"; dom("#inv-save").click(); await nextTick();
          check(saved.version===2 && saved.fields[0].label==="Campo editado","save edits");
          check(saved.fields[0].definition===catalog.fields[0].definition,"preserve metadata");
          dom("#inv-download").click(); await nextTick();
          check(JSON.stringify(await (await fetch(downloaded)).json())===JSON.stringify(saved),"download");
          const select=zip.querySelector(".inv-batch"); select.value="27"; select.dispatchEvent(new Event("change"));
          await nextTick(); check(selected==="27" && zip.querySelector(".inv-range").textContent==="1–20 of 21","external selection");
          zip.querySelector(".inv-next").click(); await nextTick();
          check(pages.includes(20) && zip.querySelector(".inv-results").textContent.includes("documento-20"),"next");
          zip.querySelector(".inv-prev").click(); await nextTick();
          check(zip.querySelector(".inv-range").textContent==="1–20 of 21","previous");
          document.body.dataset.outcome="pass";
        })().catch(e=>{document.body.dataset.outcome="failure: "+e.message;});
        """
        self.run_controls(("00004", "00007"), harness)

    def test_pdf_fields_and_ocr_lines_have_separate_clickable_layers(self):
        harness = """
        const data = {tracking:"fixture", preview:"#original", items:[{STATE:"DONE",STAGE:"DONE"}],
          pages:[{PAGE_NO:1,WIDTH_PX:1200,HEIGHT_PX:900,IMAGE_URL:"#page1",METHOD:"native",FRAGMENT_TOTAL:1,FRAGMENT_SHOWN:1}],
          fields:[{FIELD_KEY:"invoice",LABEL:"Factura",NORMALIZED_TEXT:"DEMO",PAGE_NO:1,EVIDENCE_BOX:[.2,.3,.5,.4]}],
          lines:[{PAGE_NO:1,FRAGMENT_NO:1,TEXT_VALUE:"Otro texto",EVIDENCE_BOX:"[0.1,0.5,0.2,0.6]"}]};
        window.apex={server:{process:async()=>data}};
        InvoiceMonitor.start();
        setTimeout(()=>{
          try {
            const toolbar=document.querySelector(".inv-page-controls"), spread=document.querySelector(".inv-split");
            if(toolbar.parentElement!==spread.parentElement || toolbar.nextElementSibling!==spread) throw Error("shared toolbar above columns");
            const open=toolbar.querySelector(".inv-open-original");
            let opened; window.open=url=>{opened=url;};
            open.click(); if(open.disabled || opened!=="#original") throw Error("open original PDF");
            const field=document.querySelector(".inv-match button"); field.click();
            const mark=document.querySelector(".inv-box");
            if(document.activeElement!==mark || mark.getAttribute("aria-pressed")!=="true") throw Error("field to box");
            mark.click(); if(document.activeElement!==field) throw Error("box to field");
            const layer=document.querySelectorAll(".inv-page-controls select")[1];
            layer.value="ocr"; layer.dispatchEvent(new Event("change"));
            const only=document.querySelectorAll(".inv-box");
            const results=document.querySelector(".inv-results").textContent;
            if(only.length!==1 || only[0].dataset.key!=="line-1" || !results.includes("Otro texto") || !results.includes("PDF text")) throw Error("native text layer");
            const file=document.createElement("input"); file.type="file"; file.id="P6_FILE"; document.body.append(file);
            const upload=new DataTransfer(); upload.items.add(new File(["%PDF-1.4"],"synthetic.pdf",{type:"application/pdf"}));
            file.files=upload.files; file.dispatchEvent(new Event("change",{bubbles:true}));
            open.click();
            if(!opened.startsWith("blob:") || !layer.disabled || !document.querySelector(".inv-preview iframe")) throw Error("original PDF toolbar");
            document.body.dataset.outcome="pass";
          } catch(e) { document.body.dataset.outcome=e.message; }
        },20);
        """
        self.run_controls(("00006",), harness)

    def run_controls(self, page_numbers, harness):
        regions = []
        for number in page_numbers:
            source = next((APP / "pages").glob(f"p{number}-*.apx")).read_text(encoding="utf8")
            regions.append(re.search(r"```html\n(.*?)\n```", source, re.S)[1])
        code = "\n".join((FILES / filename).read_text(encoding="utf8") for filename in ("invoice-editor.js", "invoice-monitor.js"))
        catalog = json.loads((ROOT / "source/invoice-catalog.json").read_text(encoding="utf8"))
        styles = (FILES / "invoice.css").read_text(encoding="utf8")
        html = "<!doctype html><meta charset=utf-8><style>" + styles + "</style><body>" + "".join(regions)
        html += "<script>const catalog=" + json.dumps(catalog) + ";\n" + code + "\n" + harness + "</script>"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controls.html"
            path.write_text(html, encoding="utf8")
            run = subprocess.run([BROWSER, "--headless", "--disable-gpu", "--disable-background-networking",
                                  "--no-first-run", "--user-data-dir=" + str(Path(temp) / "profile"),
                                  "--dump-dom", "--virtual-time-budget=4000", path.as_uri()],
                                 capture_output=True, timeout=45)
            result = re.search(rb'<body data-outcome="([^"]+)"', run.stdout)
            self.assertEqual(result[1] if result else None, b"pass")
