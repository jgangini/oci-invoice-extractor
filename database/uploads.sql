create or replace package inv_upload authid definer as
 procedure submit_zip(p_prefix varchar2,p_filename varchar2,p_file blob,p_object_key out varchar2);
 procedure test_pdf(p_version_id number,p_filename varchar2,p_file blob,p_test_id out varchar2);
 function test_page_content(p_test_id varchar2,p_page_no number) return blob;
end;
/
create or replace package body inv_upload as
 function test_page_content(p_test_id varchar2,p_page_no number) return blob is
  v_image blob;
 begin
  if inv_ui.permitted('REVIEW')<>1 then raise_application_error(-20003,'You do not have permission to perform this action.'); end if;
  select g.picture into v_image from inv_test_page g join inv_pdf_test t on t.test_id=g.test_id
   where g.test_id=p_test_id and g.page_no=p_page_no and t.state='DONE' and t.created_at>systimestamp-interval '1' day
   and t.actor=upper(sys_context('APEX$SESSION','APP_USER'));
  return v_image;
 end;
 procedure check_file(p_file blob,p_filename varchar2,p_extension varchar2,p_limit number,p_magic varchar2) is
 begin
  if inv_ui.permitted('REVIEW')<>1 then raise_application_error(-20003,'You do not have permission to perform this action.'); end if;
  if p_filename is null or length(p_filename)>255 or instr(p_filename,'/')>0 or instr(p_filename,chr(92))>0
     or regexp_like(p_filename,'[[:cntrl:]]') or lower(substr(p_filename,-length(p_extension)))<>p_extension
     or p_file is null or dbms_lob.getlength(p_file) not between 1 and p_limit
     or rawtohex(dbms_lob.substr(p_file,length(p_magic)/2,1))<>p_magic then
   raise_application_error(-20021,'Check the file name, format, and size, then upload it again.');
  end if;
 end;
 procedure submit_zip(p_prefix varchar2,p_filename varchar2,p_file blob,p_object_key out varchar2) is
  v_url varchar2(4000); v_prefix varchar2(512);
 begin
  check_file(p_file,p_filename,'.zip',48*1024*1024,'504B0304');
  select s.object_base_url,r.prefix into v_url,v_prefix from inv_settings s join inv_route r on r.prefix=p_prefix
   where s.id=1 and substr(r.prefix,1,length(s.source_prefix))=s.source_prefix;
  if v_url is null then raise_application_error(-20022,'The source bucket has not been configured.'); end if;
  p_object_key:=v_prefix||lower(rawtohex(sys_guid()))||'.zip';
  v_url:=v_url||replace(utl_url.escape(p_object_key,true,'AL32UTF8'),'%2F','/');
  execute immediate q'~begin dbms_cloud.put_object(credential_name=>'OCI$RESOURCE_PRINCIPAL',object_uri=>:url,contents=>:contents); end;~'
    using v_url,p_file;
 end;
 procedure test_pdf(p_version_id number,p_filename varchar2,p_file blob,p_test_id out varchar2) is
  v_url varchar2(2048); v_payload varchar2(150); v_count number; v_actor inv_operator.username%type;
 begin
  check_file(p_file,p_filename,'.pdf',12*1024*1024,'255044462D');
  select invoke_url into v_url from inv_settings where id=1;
  if v_url is null then raise_application_error(-20008,'The OCI Functions endpoint is missing.'); end if;
  select username into v_actor from inv_operator where username=upper(sys_context('APEX$SESSION','APP_USER')) for update;
  delete from inv_pdf_test where created_at < systimestamp-interval '1' day;
  select count(*) into v_count from inv_pdf_test where actor=v_actor
   and state in ('QUEUED','RUNNING') and updated_at>systimestamp-interval '10' minute;
  if v_count>=5 then raise_application_error(-20023,'You have five active PDF tests. Wait for one to finish before starting another.'); end if;
  p_test_id:=lower(rawtohex(sys_guid()));
  insert into inv_pdf_test(test_id,actor,version_id,filename,file_data)
   values(p_test_id,v_actor,p_version_id,p_filename,p_file);
  select json_object('preview_id' value p_test_id) into v_payload from dual;
  commit;
  begin
   execute immediate q'~declare r dbms_cloud_types.resp; begin
    r:=dbms_cloud.send_request(credential_name=>'OCI$RESOURCE_PRINCIPAL',uri=>:url,method=>'POST',
     headers=>'{"Content-Type":"application/json","Fn-Invoke-Type":"detached"}',body=>utl_raw.cast_to_raw(:payload));
    if dbms_cloud.get_response_status_code(r) not between 200 and 299 then raise_application_error(-20024,'The PDF test could not be started.'); end if;
   end;~' using v_url,v_payload;
  exception when others then
   update inv_pdf_test set state='ERROR',stage='ERROR',error_code='dispatch_failed',file_data=null,updated_at=systimestamp
    where test_id=p_test_id and state='QUEUED';
   commit;
  end;
 end;
end;
/
create or replace package inv_test_worker authid definer as
 procedure claim(p_id varchar2,p_claim varchar2,p_file out blob,p_template out clob);
 procedure stage(p_id varchar2,p_claim varchar2,p_stage varchar2);
 procedure save_page(p_id varchar2,p_claim varchar2,p_page number,p_width number,p_height number,p_image blob,p_total number,p_shown number,p_lines clob,p_method varchar2 default null);
 procedure finish(p_id varchar2,p_claim varchar2,p_result clob,p_error varchar2);
end;
/
create or replace package body inv_test_worker as
 procedure claim(p_id varchar2,p_claim varchar2,p_file out blob,p_template out clob) is
 begin
  update inv_pdf_test set state='RUNNING',stage='READ_PDF',claim_id=p_claim,updated_at=systimestamp
   where test_id=p_id and created_at>systimestamp-interval '1' day
    and (state='QUEUED' or (state='RUNNING' and updated_at<systimestamp-interval '5' minute));
  if sql%rowcount=1 then
   select p.file_data,t.definition into p_file,p_template from inv_pdf_test p join inv_template t on t.version_id=p.version_id
    where p.test_id=p_id;
  end if;
 end;
 procedure stage(p_id varchar2,p_claim varchar2,p_stage varchar2) is
 begin
  if p_stage not in ('OCR','EXTRACT_FIELDS','LOCATE_EVIDENCE') then raise_application_error(-20025,'The PDF test stage is invalid.'); end if;
  update inv_pdf_test set stage=p_stage,updated_at=systimestamp where test_id=p_id and claim_id=p_claim and state='RUNNING';
 end;
 procedure finish(p_id varchar2,p_claim varchar2,p_result clob,p_error varchar2) is
 begin
  update inv_pdf_test set state=case when p_error is null then 'DONE' else 'ERROR' end,
   stage=case when p_error is null then 'DONE' else 'ERROR' end,error_code=p_error,
   result_json=p_result,file_data=null,updated_at=systimestamp where test_id=p_id and claim_id=p_claim and state='RUNNING';
 end;
 procedure save_page(p_id varchar2,p_claim varchar2,p_page number,p_width number,p_height number,p_image blob,p_total number,p_shown number,p_lines clob,p_method varchar2 default null) is
  v_id varchar2(32);
 begin
  if dbms_lob.getlength(p_image)>262144 then raise_application_error(-20026,'The page preview exceeds the size limit.'); end if;
  select test_id into v_id from inv_pdf_test where test_id=p_id and state='RUNNING' and claim_id=p_claim for update;
  insert into inv_test_page(test_id,page_no,width_px,height_px,picture,ocr_total,ocr_shown,ocr_lines,text_method)
   values(v_id,p_page,p_width,p_height,p_image,p_total,p_shown,p_lines,p_method);
 exception when no_data_found then null;
 end;
end;
/
create or replace view inv_v_upload_routes as
 select r.prefix,t.name,t.version_no,r.target_key from inv_route r join inv_template t on t.version_id=r.version_id
 where inv_ui.permitted('REVIEW')=1
/
create or replace view inv_v_test_templates as
 select version_id,name,version_no from inv_template where inv_ui.permitted('REVIEW')=1
/
create or replace view inv_v_tests as
 select test_id,version_id,filename,
 case when state in ('QUEUED','RUNNING') and updated_at<systimestamp-interval '10' minute then 'ERROR' else state end state,
 case when state in ('QUEUED','RUNNING') and updated_at<systimestamp-interval '10' minute then 'ERROR' else stage end stage,
 created_at,updated_at,
 case when state in ('QUEUED','RUNNING') and updated_at<systimestamp-interval '10' minute then 'worker_timeout' else error_code end error_code
 from inv_pdf_test
 where actor=upper(sys_context('APEX$SESSION','APP_USER')) and inv_ui.permitted('REVIEW')=1
 and created_at>systimestamp-interval '1' day
/
create or replace view inv_v_test_pages as
 with pages as (
  select g.test_id,g.page_no,g.width_px,g.height_px,
   coalesce(g.text_method,case when g.ocr_total is null then 'native' else 'ocr' end) method,
   nvl(g.ocr_total,0) fragment_total,g.ocr_shown fragment_shown,
   case when g.ocr_total>g.ocr_shown then 1 else 0 end fragment_truncated
  from inv_test_page g join inv_v_tests v on v.test_id=g.test_id where v.state='DONE'
 )
 select test_id,page_no,width_px,height_px,method,case when method='ocr' then fragment_total else 0 end ocr_total,
  case when method='ocr' then fragment_shown else 0 end ocr_shown,
  case when method='ocr' then fragment_truncated else 0 end ocr_truncated,fragment_total,fragment_shown,fragment_truncated
 from pages
/
create or replace view inv_v_test_fragments as
 select g.test_id,g.page_no,j.fragment_no,j.text_value,j.evidence_box,j.text_truncated,v.method
 from inv_v_test_pages v join inv_test_page g on g.test_id=v.test_id and g.page_no=v.page_no
 cross apply json_table(g.ocr_lines,'$[*]' columns(fragment_no for ordinality,text_value varchar2(500 char) path '$.text',
 evidence_box varchar2(128) format json path '$.box',text_truncated number path '$.cut')) j
/
create or replace view inv_v_test_fields as
 select p.test_id,j.field_key,f.label,j.status,j.original_text,j.normalized_text,j.page_no,j.method,j.evidence,
 j.evidence_box,j.anchor_text
 from inv_v_tests v join inv_pdf_test p on p.test_id=v.test_id
 cross apply json_table(p.result_json,'$[*]' columns (
   field_key varchar2(64) path '$.key',status varchar2(12) path '$.status',original_text varchar2(2000 char) path '$.original',
   normalized_text varchar2(2000 char) path '$.normalized',page_no number path '$.page',method varchar2(8) path '$.method',
   evidence varchar2(250 char) path '$.evidence', evidence_box varchar2(128) format json path '$.box',
   anchor_text varchar2(250 char) path '$.anchor_text')) j
 join inv_field f on f.version_id=p.version_id and f.field_key=j.field_key
/
