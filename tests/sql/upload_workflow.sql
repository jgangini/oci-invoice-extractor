-- Run as INV_DATA in a disposable, seeded test database without an APEX session.
whenever sqlerror exit failure rollback
set serveroutput on
declare
  v_batch number; v_template number; v_target varchar2(64); v_run number; v_count number;
  v_done clob; v_fields clob; v_state varchar2(12); v_return varchar2(1024);
  v_key varchar2(100):='local-qa/'||lower(rawtohex(sys_guid()))||'.zip';
  procedure check_count(n number, expected number, label varchar2) is
  begin
    if n is null or n<>expected then raise_application_error(-20101,label); end if;
  end;
  procedure cleanup is
  begin
    delete from inv_result where run_id in (select r.run_id from inv_run r join inv_document d
      on d.document_id=r.document_id where d.batch_id=v_batch);
    delete from inv_doc_checkpoint where document_id in (select document_id from inv_document where batch_id=v_batch);
    delete from inv_run where document_id in (select document_id from inv_document where batch_id=v_batch);
    delete from inv_document where batch_id=v_batch;
    delete from inv_batch where batch_id=v_batch and object_key=v_key;
    commit;
  end;
begin
  check_count(inv_ui.permitted('REVIEW'),0,'A direct session may not act as an APEX operator');
  begin
    inv_upload.submit_zip('source/','test.zip',empty_blob(),v_return);
    raise_application_error(-20102,'Unauthenticated upload unexpectedly accepted');
  exception when others then if sqlcode<>-20003 then raise; end if;
  end;
  select min(version_id) into v_template from inv_template;
  select min(target_key) into v_target from inv_target;
  insert into inv_batch(object_key,etag,route_prefix,target_key,version_id)
    values(v_key,'qa','local-qa/',v_target,v_template) returning batch_id into v_batch;
  inv_ingest.index_members(v_batch,1,'["a.pdf","b.pdf"]',v_done);
  commit;
  select expected_docs into v_count from inv_batch where batch_id=v_batch;
  check_count(v_count,2,'Archive manifest must publish both PDFs before extraction');
  inv_ingest.start_member(v_batch,1,'a.pdf','qa',v_run);
  select count(*) into v_count from inv_doc_checkpoint c join inv_document d on d.document_id=c.document_id
    where d.batch_id=v_batch and c.finished_at is null;
  check_count(v_count,1,'Starting a PDF must publish its checkpoint');
  inv_ingest.fail_member(v_run,'fixture_error');
  commit;
  inv_ingest.index_members(v_batch,1,'["a.pdf","b.pdf"]',v_done);
  select count(*) into v_count from json_table(v_done,'$[*]' columns(member_name varchar2(100) path '$')) where member_name='a.pdf';
  check_count(v_count,1,'A failed PDF must be terminal on continuation');
  inv_ingest.start_member(v_batch,1,'b.pdf','qa',v_run);
  select json_arrayagg(json_object('key' value field_key,'status' value 'absent') returning clob)
    into v_fields from inv_field where version_id=v_template;
  inv_ingest.save_fields(v_run,v_fields);
  inv_ingest.finish_batch(v_batch,1,2);
  select state into v_state from inv_batch where batch_id=v_batch;
  if v_state<>'REVIEW' then raise_application_error(-20103,'The batch must complete with review'); end if;
  select count(*) into v_count from inv_doc_checkpoint c join inv_document d on c.document_id=d.document_id
    where d.batch_id=v_batch and c.finished_at is not null;
  check_count(v_count,2,'Both documents, including the error, must be counted as completed');
  cleanup;
  dbms_output.put_line('PASS: direct-session denial, archive totals, document checkpoints, continuation, terminal errors.');
exception when others then
  rollback; cleanup; raise;
end;
/
declare
  v_test varchar2(32):=lower(rawtohex(sys_guid()));
  v_claim varchar2(32):=lower(rawtohex(sys_guid()));
  v_template number; v_count number;
begin
  select min(version_id) into v_template from inv_template;
  insert into inv_pdf_test(test_id,actor,version_id,filename,state,claim_id)
    values(v_test,'LOCAL-QA',v_template,'sample.pdf','RUNNING',v_claim);
  inv_test_worker.save_page(v_test,v_claim,1,100,200,empty_blob(),1,1,'[{"text":"Sample","box":[0.1,0.2,0.5,0.3],"cut":0}]','native');
  inv_test_worker.save_page(v_test,v_claim,2,100,200,empty_blob(),3,1,'[{"text":"Scan","box":[0.1,0.2,0.5,0.3],"cut":0}]');
  select count(*) into v_count from inv_test_page where test_id=v_test and
    ((page_no=1 and text_method='native' and ocr_total=1 and ocr_shown=1)
     or (page_no=2 and text_method is null and ocr_total=3 and ocr_shown=1));
  if v_count<>2 then raise_application_error(-20104,'Native or legacy OCR page metadata missing'); end if;
  rollback;
  dbms_output.put_line('PASS: explicit native page source and legacy OCR worker.');
exception when others then rollback; raise;
end;
/
