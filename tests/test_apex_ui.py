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
          if (name === "ABRIR") return {definition:JSON.stringify({...catalog, version:3})};
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
          check(dom("#inv-name").value===catalog.name && !dom("#inv-version, #inv-number"),"open the only saved template");
          const transfer=new DataTransfer();
          transfer.items.add(new File([JSON.stringify({...catalog,name:"Imported catalog",version:9})],"plantilla.json",{type:"application/json"}));
          const input=dom("#inv-json-file"); input.files=transfer.files; input.dispatchEvent(new Event("change"));
          for(let i=0; i<100 && dom("#inv-name").value!=="Imported catalog"; i++) await nextTick();
          check(dom("#inv-name").value==="Imported catalog" && editor.querySelectorAll("details").length===12,"import");
          const label=editor.querySelector("details input");
          label.value="Campo editado"; label.dispatchEvent(new Event("input",{bubbles:true}));
          dom("#inv-save").click(); await nextTick();
          check(!Object.hasOwn(saved,"version") && saved.name==="Imported catalog" && saved.fields[0].label==="Campo editado","save edits without versioning");
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
        const data = {tracking:"fixture", templateName:"Invoice Catalog", preview:"#original", items:[{STATE:"DONE",STAGE:"DONE",TEMPLATE_NAME:"Previous catalog"}],
          pages:[{PAGE_NO:1,WIDTH_PX:1200,HEIGHT_PX:900,IMAGE_URL:"data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",METHOD:"native",FRAGMENT_TOTAL:2,FRAGMENT_SHOWN:2}],
          fields:[{FIELD_KEY:"invoice",LABEL:"Factura",STATUS:"found",NORMALIZED_TEXT:"DEMO",ANCHOR_TEXT:"Raw OCR excerpt",PAGE_NO:1,EVIDENCE_BOX:[.2,.3,.5,.4]},
                  {FIELD_KEY:"missing",LABEL:"Issue date",STATUS:"absent"}],
          lines:[{PAGE_NO:1,FRAGMENT_NO:1,TEXT_VALUE:"Otro texto",EVIDENCE_BOX:"[0.1,0.5,0.2,0.6]"},
                 {PAGE_NO:1,FRAGMENT_NO:2,TEXT_VALUE:"Otra región",EVIDENCE_BOX:"[0.3,0.5,0.4,0.6]"}]};
        for(let i=0;i<30;i++) data.fields.push({FIELD_KEY:"fixture-"+i,LABEL:"Field "+i,NORMALIZED_TEXT:"Value"});
        let notifyUpload, uploadCount=0, tracked;
        const awaitUpload=()=>new Promise(resolve=>{notifyUpload=resolve;});
        // Chromium's virtual-time budget can expire while a native FileReader task is pending.
        window.FileReader=class {
          readAsDataURL(blob) {
            const url=URL.createObjectURL(blob);
            try {
              const request=new XMLHttpRequest();
              request.open("GET",url,false);
              request.send();
              this.result="data:application/pdf;base64,"+btoa(request.responseText);
              this.onload();
            } finally { URL.revokeObjectURL(url); }
          }
        };
        window.apex={
          server:{chunk:text=>text.match(/.{1,8000}/g),process:(name,args,options)=>{
            if(name==="PROBAR") {
              uploadCount++;
              return new Promise((resolve,reject)=>{notifyUpload({args,options,resolve,reject});});
            }
            if(args.x01) tracked=args.x01;
            return Promise.resolve(data);
          }},
          item:id=>({setValue:value=>{document.getElementById(id).value=value;}})
        };
        InvoiceMonitor.start();
        setTimeout(()=>{
          try {
            const toolbar=document.querySelector(".inv-page-controls"), spread=document.querySelector(".inv-split");
            const docTitle=spread.querySelector("#inv-documento"), fieldsTitle=spread.querySelector("#inv-campos");
            const templateTitle=document.querySelector(".inv-template-heading");
            const filter=spread.querySelector(".inv-field-filter");
            const fieldName=spread.querySelector(".inv-field-name");
            if(templateTitle.hidden || templateTitle.textContent!=="Previous catalog" || !templateTitle.nextElementSibling.matches(".inv-timeline"))
              throw Error("the template used for this extraction appears above the timeline");
            const rem=getComputedStyle(document.documentElement).fontSize;
            if([templateTitle,docTitle,fieldsTitle].some(title=>getComputedStyle(title).fontSize!==rem || getComputedStyle(title).fontWeight!=="650"))
              throw Error("review headings match the region heading typography");
            if(toolbar.closest("section")!==spread.firstElementChild || toolbar.previousElementSibling!==docTitle) throw Error("document title above controls");
            if(Math.abs(docTitle.getBoundingClientRect().top-fieldsTitle.getBoundingClientRect().top)>1) throw Error("matching panel headers");
            if(filter.disabled || filter.getBoundingClientRect().top<=fieldsTitle.getBoundingClientRect().bottom) throw Error("filter below title");
            if(fieldsTitle.textContent!=="Extracted fields (32/1)") throw Error("total and found in heading");
            if(document.querySelector(".inv-open-original")) throw Error("duplicate PDF action");
            const status=document.querySelector(".inv-status");
            if(getComputedStyle(status).position!=="absolute") throw Error("completed status shouldn't occupy a row");
            const image=spread.querySelector("img"), panel=spread.firstElementChild.getBoundingClientRect();
            const documentLoader=spread.querySelector(".inv-document-loader");
            if(documentLoader.dataset.state!=="ready" || image.closest(".inv-preview").inert) throw Error("loaded evidence is revealed");
            if(getComputedStyle(spread).borderTopWidth!=="1px" || parseFloat(getComputedStyle(spread).borderTopLeftRadius)<=0)
              throw Error("the two review panes share a rounded border");
            const page=image.getBoundingClientRect(), resultsPanel=spread.lastElementChild.getBoundingClientRect();
            const resultsArea=spread.querySelector(".inv-results");
            const [found, missing]=resultsArea.querySelectorAll(".inv-match");
            if(Math.abs(found.getBoundingClientRect().width-resultsArea.clientWidth)>1 ||
               Math.abs(missing.getBoundingClientRect().left-resultsArea.getBoundingClientRect().left)>1) throw Error("full-width result rows");
            const foundTag=found.querySelector(".inv-field-status"), missingTag=missing.querySelector(".inv-field-status");
            if(fieldName.disabled || fieldName.options[1].text!=="Factura" ||
               fieldName.getBoundingClientRect().left>=filter.getBoundingClientRect().left) throw Error("field selector at the left");
            if(Math.abs(found.querySelector("h3").offsetHeight-missing.querySelector("h3").offsetHeight)>1) throw Error("even field title sizing");
            if(document.querySelector(".inv-refresh")) throw Error("no PDF refresh button");
            if(foundTag?.textContent!=="Found" || missingTag?.textContent!=="Missing") throw Error("field status tags");
            if(getComputedStyle(foundTag).backgroundColor===getComputedStyle(missingTag).backgroundColor) throw Error("status colors");
            if(getComputedStyle(missing).backgroundColor==="rgba(0, 0, 0, 0)" ||
               getComputedStyle(missing).backgroundColor===getComputedStyle(found).backgroundColor) throw Error("missing row has its own fill");
            if(missingTag.getBoundingClientRect().left<=missing.querySelector("h3").getBoundingClientRect().right) throw Error("status aligned to the right");
            if(missing.querySelector("strong, small, p") || found.querySelector("p") || found.textContent.includes("Raw OCR excerpt"))
              throw Error("redundant field text");
            const valueStyle=getComputedStyle(found.querySelector("strong"));
            if(Math.abs(parseFloat(valueStyle.fontSize) - parseFloat(rem)*.9)>.01 ||
               valueStyle.fontWeight!=="400" || valueStyle.color!=="rgb(0, 0, 0)") throw Error("plain black field values");
            if(Math.abs(page.width-panel.width)>1 || Math.abs(page.bottom-panel.bottom)>1) throw Error("full-bleed document");
            if(getComputedStyle(image.parentElement).boxShadow!=="none") throw Error("document shadow");
            if(Math.abs(resultsPanel.height-panel.height)>1 || resultsArea.scrollHeight<=resultsArea.clientHeight) throw Error("results contained by document height");
            resultsArea.scrollTop=100;
            if(resultsArea.scrollTop!==100) throw Error("results scroll independently");
            resultsArea.scrollTop=0;
            const mask=spread.querySelector(".inv-evidence-mask");
            found.dispatchEvent(new PointerEvent("pointerenter",{pointerType:"mouse"}));
            if(!mask.classList.contains("is-active") || getComputedStyle(mask).clipPath==="none" ||
               getComputedStyle(mask).pointerEvents!=="none" || !spread.querySelector(".inv-box.is-spotlit")) throw Error("hover spotlights the PDF");
            if(!matchMedia("(prefers-reduced-motion: reduce)").matches && getComputedStyle(mask).transitionProperty!=="opacity") throw Error("spotlight cross-fade");
            found.dispatchEvent(new PointerEvent("pointerleave",{pointerType:"mouse"}));
            if(mask.classList.contains("is-active")) throw Error("hover spotlight removed when leaving");
            const field=document.querySelector(".inv-match button"); field.click();
            if(getComputedStyle(field).textDecorationLine!=="none") throw Error("field title not underlined");
            const mark=document.querySelector(".inv-box");
            if(document.activeElement!==mark || mark.getAttribute("aria-pressed")!=="true") throw Error("field to box");
            mark.click(); if(document.activeElement!==field) throw Error("box to field");
            const pick=status=>{filter.value=status; filter.dispatchEvent(new Event("change"));};
            pick("absent");
            if(spread.querySelector("img")!==image || documentLoader.dataset.state!=="ready") throw Error("filter changes keep the loaded document");
            if(resultsArea.querySelectorAll(".inv-match").length!==1 || !resultsArea.textContent.includes("Issue date") ||
               spread.querySelector(".inv-box")) throw Error("filter missing fields and marks");
            if(fieldsTitle.textContent!=="Extracted fields (32/1)") throw Error("count unaffected by filters");
            pick("ambiguous");
            if(!resultsArea.textContent.includes("No fields match these filters.")) throw Error("empty status filter");
            pick("absent");
            const layer=document.querySelectorAll(".inv-page-controls select")[1];
            layer.value="ocr"; layer.dispatchEvent(new Event("change"));
            const only=document.querySelectorAll(".inv-box");
            const results=document.querySelector(".inv-results").textContent;
            if(only.length!==2 || only[0].dataset.key!=="line-1" || !results.includes("Otro texto") || !results.includes("PDF text") || resultsArea.querySelector(".inv-field-status")) throw Error("native text layer");
            if(fieldsTitle.textContent!=="Recognized text (2)" || spread.querySelector(".inv-ocr-summary")) throw Error("recognized count only in heading");
            const region=spread.querySelector(".inv-region-filter");
            if(region.options[2].text!=="Text region 2" || region.closest(".inv-region-controls").hidden) throw Error("region choices shown");
            region.value="line-2"; region.dispatchEvent(new Event("change"));
            if(resultsArea.querySelectorAll(".inv-match").length!==1 || !resultsArea.textContent.includes("Otra región") ||
               spread.querySelectorAll(".inv-box").length!==1 || !spread.querySelector(".inv-box[data-key='line-2']")) throw Error("filter recognized region");
            if(!filter.closest(".inv-field-controls").hidden) throw Error("field filter hidden in recognized text");
            layer.value="fields"; layer.dispatchEvent(new Event("change"));
            if(!region.closest(".inv-region-controls").hidden) throw Error("region filter hidden in template fields");
            if(filter.value!=="absent" || resultsArea.querySelectorAll(".inv-match").length!==1) throw Error("restore status filter after overlay switch");
            pick("found");
            if(resultsArea.querySelectorAll(".inv-match").length!==1 || !spread.querySelector(".inv-box[data-key='invoice']")) throw Error("filter found and keep mark");
            fieldName.value="missing"; fieldName.dispatchEvent(new Event("change"));
            if(resultsArea.querySelector(".inv-match")) throw Error("combined field and status without matches");
            pick("");
            if(resultsArea.querySelectorAll(".inv-match").length!==1 || !resultsArea.textContent.includes("Issue date")) throw Error("filter by field");
            fieldName.value=""; fieldName.dispatchEvent(new Event("change"));
            if(resultsArea.querySelectorAll(".inv-match").length!==data.fields.length) throw Error("reset status filter");
            const file=document.createElement("input"); file.type="file"; file.id=file.name="P6_FILE";
            document.querySelector(".t-Region-bodyWrap").append(file);
            const run=document.querySelector(".inv-pdf-run");
            if(run.textContent!=="New execution" || run.disabled) throw Error("completed result supports starting a new execution");
            run.click();
            if(!run.disabled) throw Error("empty form disables extraction");
            if(!templateTitle.hidden) throw Error("a new execution clears the previous template heading");
            const upload=new DataTransfer(); upload.items.add(new File(["%PDF-1.4"],"synthetic.pdf",{type:"application/pdf"}));
            file.files=upload.files; file.dispatchEvent(new Event("change"));
            const preview=documentLoader.querySelector(".inv-preview");
            if(preview.querySelector("iframe, img") || !layer.disabled ||
               getComputedStyle(spread).display!=="none") throw Error("preview stays hidden until submit");
            if(!status.textContent.includes("Ready to extract: synthetic.pdf")) throw Error("selection feedback");
            if(run.disabled || getComputedStyle(spread).display!=="none") throw Error("a valid PDF is ready to use the current catalog");
            const invalid=new DataTransfer(); invalid.items.add(new File(["invalid"],"invalid.txt"));
            file.files=invalid.files; file.dispatchEvent(new Event("change"));
            if(!status.classList.contains("is-error") || getComputedStyle(status).position==="absolute") throw Error("file errors are visible");
            if(!run.disabled) throw Error("invalid file disables extraction");
            file.value=""; file.dispatchEvent(new Event("change"));
            setTimeout(async()=>{
              try {
                if(getComputedStyle(spread).display!=="none" || resultsArea.querySelector(".inv-match")) throw Error("removing a file keeps past results hidden");
                file.files=upload.files; file.dispatchEvent(new Event("change"));
                if(run.disabled || getComputedStyle(spread).display!=="none") throw Error("review waits for extraction");
                const request=awaitUpload();
                run.click();
                if(!run.disabled || getComputedStyle(run,"::before").animationName!=="inv-pdf-spin") throw Error("inline spinner and blocked action on submit");
                if(getComputedStyle(document.querySelector(".t-Region-bodyWrap")).display!=="none") throw Error("upload form is hidden during submit");
                const first=await request;
                if(first.args.pageItems || Object.hasOwn(first.args,"x03") || first.args.x04!=="synthetic.pdf" ||
                   atob(first.args.f01.join(""))!=="%PDF-1.4" || !(first.options.timeout>0)) throw Error("Ajax upload sends file bytes without setting session state");
                if(resultsArea.closest(".t-skel").dataset.state!=="loading" || !resultsArea.inert) throw Error("upload shows the placeholder");
                if(documentLoader.dataset.state!=="loading" || !preview.inert || preview.querySelector("iframe, img"))
                  throw Error("document remains concealed while the upload is pending");
                if(!status.textContent.includes("Submitting PDF") || getComputedStyle(spread).display==="none" ||
                   document.querySelector('[aria-current="step"]').textContent!=="ReceivedIn progress") throw Error("submit immediately opens the live timeline");
                if(templateTitle.hidden || templateTitle.textContent!=="Invoice Catalog") throw Error("the current template appears during upload");
                const placeholder=documentLoader.querySelector(".inv-document-placeholder");
                const documentBounds=documentLoader.getBoundingClientRect(), placeholderBounds=placeholder.getBoundingClientRect();
                if(documentBounds.height<=0 || ["top","right","bottom","left"].some(edge=>Math.abs(documentBounds[edge]-placeholderBounds[edge])>1))
                  throw Error("document skeleton covers the full preview");
                if(placeholder.children[2].getBoundingClientRect().height<documentBounds.height/2)
                  throw Error("document skeleton shows rows through the preview");
                run.click();
                if(uploadCount!==1) throw Error("no duplicate upload while waiting for confirmation");
                first.reject(new Error("network error"));
                await new Promise(resolve=>setTimeout(resolve,0));
                if(run.disabled || !status.classList.contains("is-error") || getComputedStyle(spread).display!=="none") throw Error("failed upload restores the form and shows an error");
                const split=new DataTransfer();
                split.items.add(new File(["%PDF-1.4", new Uint8Array(384*1024)],"synthetic.pdf",{type:"application/pdf"}));
                file.files=split.files; file.dispatchEvent(new Event("change"));
                const opening=awaitUpload();
                run.click();
                const part=await opening;
                if(part.args.x02!=="0" || part.args.x05!=="N" || atob(part.args.f01.join("")).length!==384*1024)
                  throw Error("large PDF begins with a bounded part");
                const finishing=awaitUpload();
                part.resolve({received:true});
                const last=await finishing;
                if(last.args.x01!==part.args.x01 || last.args.x02!=="1" || last.args.x05!=="Y") throw Error("last part belongs to the same ordered upload");
                data.tracking="second-extraction";
                data.pages[0].IMAGE_URL+="#second-extraction";
                data.fields[0].NORMALIZED_TEXT="SECOND";
                last.resolve({tracking:data.tracking});
                await new Promise(resolve=>setTimeout(resolve,20));
                if(tracked!==data.tracking || file.value || !resultsArea.textContent.includes("SECOND") || resultsArea.inert)
                  throw Error("accepted upload is tracked and revealed on the same page");
                const secondImage=spread.querySelector(".inv-page-image img");
                if(secondImage===image || secondImage?.getAttribute("src")!==data.pages[0].IMAGE_URL)
                  throw Error("the same page number displays the new extraction's image");
                document.body.dataset.outcome="pass";
              } catch(e) { document.body.dataset.outcome=e.message; }
            },20);
          } catch(e) { document.body.dataset.outcome=e.message; }
        },20);
        """
        self.run_controls(("00006",), harness, """
            <form class="inv-pdf-input">
              <button type="button" class="inv-pdf-run">Run extraction</button>
              <div class="t-Region-bodyWrap"></div>
            </form>
        """)

    def test_pdf_tracks_live_stages_and_resets_when_starting_a_new_execution(self):
        harness = """
        let row={STATE:"QUEUED", STAGE:"QUEUED"}, fields=[], nextPoll;
        window.apex={server:{process:async()=>({tracking:"fixture", preview:"#original", items:[row], fields})}};
        const schedule=window.setTimeout;
        window.setTimeout=(fn, ms)=>ms===1000 ? (nextPoll=fn, 0) : schedule(fn, ms);
        const run=document.querySelector(".inv-pdf-run");
        const form=document.querySelector(".t-Region-bodyWrap");
        const panel=document.querySelector(".inv-results").closest(".t-skel");
        const placeholder=panel.querySelector(".t-skel-skeleton");
        const results=panel.querySelector(".inv-results");
        const documentLoader=document.querySelector(".inv-document-loader");
        const preview=documentLoader.querySelector(".inv-preview");
        const check=(condition, message)=>{ if(!condition) throw Error(message); };
        const current=()=>document.querySelector('[aria-current="step"]');
        const documentPending=()=>documentLoader.dataset.state==="loading" &&
          documentLoader.getAttribute("aria-busy")==="true" && preview.inert && !preview.querySelector("iframe, img");
        InvoiceMonitor.start();
        (async()=>{
          await Promise.resolve();
          check(run.disabled && getComputedStyle(form).display==="none", "tracked job stays locked after reload");
          check(panel.dataset.state==="loading" && panel.getAttribute("aria-busy")==="true" && results.inert,
                "busy results are replaced by the placeholder");
          check(documentPending(), "reopening a queued job keeps the original PDF hidden");
          check(placeholder.getAttribute("aria-hidden")==="true" && getComputedStyle(placeholder).visibility==="visible" && !results.textContent,
                "decorative skeleton is visible in place of waiting text");
          const reducedMotion=matchMedia("(prefers-reduced-motion: reduce)").matches;
          check(getComputedStyle(placeholder.firstElementChild).animationName===(reducedMotion ? "none" : "t-skel-pulse"), "respect motion preference");
          const received=current();
          await nextPoll();
          check(current()===received, "unchanged stage keeps its animation");
          check(panel.firstElementChild===placeholder, "polling does not recreate or restart the skeleton");
          for(const [stage, title] of [["READ_PDF","Read PDF"],["OCR","Optical recognition"],
                                       ["EXTRACT_FIELDS","Extract fields"],["LOCATE_EVIDENCE","Locate evidence"]]) {
            row={STATE:"RUNNING", STAGE:stage};
            await nextPoll();
            check(current().textContent===title+"In progress", "polling advances to "+title);
            check(documentPending(), "the document stays behind the skeleton during "+title);
            check(getComputedStyle(current()).animationName==="inv-pdf-progress", "active stage is animated");
            check(current().previousElementSibling.dataset.state==="Complete", "previous stage is complete");
          }
          row={STATE:"DONE", STAGE:"DONE"};
          fields=[{FIELD_KEY:"invoice", LABEL:"Invoice number", STATUS:"found", NORMALIZED_TEXT:"DEMO"}];
          await nextPoll();
          check(current().textContent==="ResultComplete" && getComputedStyle(form).display==="none", "completed review keeps the form hidden");
          check(!run.disabled && run.textContent==="New execution" && getComputedStyle(run,"::before").animationName==="none", "completion offers a new execution");
          check(panel.dataset.state==="ready" && panel.getAttribute("aria-busy")==="false" && !results.inert && results.textContent.includes("DEMO"),
                "real results replace the busy state and become accessible");
          const frame=preview.querySelector("iframe");
          check(frame?.getAttribute("src")==="#original" && documentLoader.dataset.state==="loading" && preview.inert,
                "successful extraction without page images starts loading the original PDF");
          frame.dispatchEvent(new Event("load"));
          check(documentLoader.dataset.state==="ready" && !preview.inert, "the original PDF is revealed after loading");
          check(getComputedStyle(results).transitionDuration===(reducedMotion ? "0s" : "0.4s, 0.4s"), "reveal follows the motion preference");
          run.click();
          check(run.disabled && run.textContent==="Run extraction" && getComputedStyle(form).display!=="none", "new execution restores upload controls");
          check(getComputedStyle(document.querySelector(".inv-split")).display==="none" && !results.querySelector(".inv-match"), "previous review is hidden");
          check(document.activeElement.id==="P6_FILE", "focus returns to the PDF input");
          document.body.dataset.outcome="pass";
        })().catch(error=>{document.body.dataset.outcome=error.message;});
        """
        self.run_controls(("00006",), harness, """
            <section class="inv-pdf-input">
              <button class="inv-pdf-run">Run extraction</button>
              <div class="t-Region-bodyWrap"><input type="file" id="P6_FILE"></div>
            </section>
        """)

    def test_pdf_retries_temporary_status_error_without_refresh(self):
        harness = """
        let calls=0;
        window.apex={server:{process:async()=>{
          if(++calls===1) throw Error("temporary");
          return {};
        }}};
        const schedule=window.setTimeout;
        window.setTimeout=(fn, ms)=>schedule(fn, ms===4000 ? 5 : ms);
        InvoiceMonitor.start();
        setTimeout(()=>{
          const status=document.querySelector(".inv-status");
          const review=document.querySelector(".inv-split");
          document.body.dataset.outcome=calls===2 && !status.classList.contains("is-error") &&
            getComputedStyle(review).display==="none" ? "pass" : "automatic status retry and empty review hidden";
        },40);
        """
        self.run_controls(("00006",), harness)

    def run_controls(self, page_numbers, harness, markup=""):
        regions = []
        for number in page_numbers:
            source = next((APP / "pages").glob(f"p{number}-*.apx")).read_text(encoding="utf8")
            regions.append(re.search(r"```html\n(.*?)\n```", source, re.S)[1])
        code = "\n".join((FILES / filename).read_text(encoding="utf8") for filename in ("invoice-editor.js", "invoice-monitor.js"))
        catalog = json.loads((ROOT / "source/invoice-catalog.json").read_text(encoding="utf8"))
        styles = (FILES / "invoice.css").read_text(encoding="utf8")
        html = "<!doctype html><meta charset=utf-8><style>" + styles + "</style><body>" + markup + "".join(regions)
        html += "<script>const catalog=" + json.dumps(catalog) + ";\n" + code + "\n" + harness + "</script>"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controls.html"
            path.write_text(html, encoding="utf8")
            run = subprocess.run([BROWSER, "--headless", "--disable-gpu", "--disable-background-networking",
                                  "--no-first-run", "--window-size=1447,1018", "--user-data-dir=" + str(Path(temp) / "profile"),
                                  "--dump-dom", "--virtual-time-budget=4000", path.as_uri()],
                                 capture_output=True, timeout=45)
            result = re.search(rb'<body data-outcome="([^"]+)"', run.stdout)
            self.assertEqual(result[1] if result else None, b"pass")
