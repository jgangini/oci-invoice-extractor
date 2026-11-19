create or replace package inv_catalog authid definer as
 function current_id return number;
 procedure save_template(p_definition clob,p_id out number);
end;
/
create or replace package body inv_catalog as
 function current_id return number is v_id number;
 begin
  select max(version_id) keep(dense_rank last order by version_no) into v_id from inv_template;
  return v_id;
 end;
 procedure save_template(p_definition clob,p_id out number) is
  obj json_object_t; fields json_array_t; field json_object_t; labels json_array_t;
  nm varchar2(100); ver number; k varchar2(64); typ varchar2(12); fmt varchar2(12);
  v_label varchar2(200); v_required number; v_lock number; v_valid number; v_payload clob; v_existing clob;
 begin
  savepoint save_catalog;
  if p_definition is null or dbms_lob.getlength(p_definition)>250000 then
   raise_application_error(-20011,'Enter a valid JSON template within the size limit.'); end if;
  select case when p_definition is json strict with unique keys then 1 else 0 end into v_valid from dual;
  if v_valid=0 then raise_application_error(-20011,'The template must contain valid JSON with unique properties.'); end if;
  obj:=json_object_t.parse(p_definition); nm:=trim(obj.get_string('name'));
  fields:=obj.get_array('fields');
  if nm is null or fields is null or fields.get_size not between 1 and 100 then
   raise_application_error(-20011,'Check the template name and fields.'); end if;
  obj.remove('version');
  obj.put('name',nm);
  v_payload:=obj.to_clob();
  select id into v_lock from inv_settings where id=1 for update;
  p_id:=current_id();
  if p_id is not null then
   select definition into v_existing from inv_template where version_id=p_id;
   if dbms_lob.compare(v_existing,v_payload)=0 then return; end if;
  end if;
  select nvl(max(version_no),0)+1 into ver from inv_template;
  insert into inv_template(name,version_no,definition) values(nm,ver,v_payload) returning version_id into p_id;
  for i in 0..fields.get_size-1 loop
   field:=treat(fields.get(i) as json_object_t); k:=field.get_string('key'); typ:=field.get_string('type');
   fmt:=coalesce(field.get_string('format'),typ); labels:=field.get_array('labels');
   if k is null or not regexp_like(k,'^[a-z][a-z0-9_]{0,63}$','c') or typ is null or typ not in ('string','date','currency')
    or not (fmt=typ or (typ='string' and fmt='year')) or labels is null or labels.get_size not between 1 and 25
    or field.get_string('applicability') is null or field.get_string('applicability') not in ('required','optional','conditional') then
    raise_application_error(-20011,'The field definition is invalid.'); end if;
   if field.has('currency') and (typ<>'currency' or not field.get('currency').is_string
      or field.get_string('currency') is null or not regexp_like(field.get_string('currency'),'^[A-Z]{3}$','c')) then
    raise_application_error(-20011,'Use a three-letter currency code for currency fields.'); end if;
   for j in 0..labels.get_size-1 loop
    if not labels.get(j).is_string or trim(labels.get_string(j)) is null or length(labels.get_string(j))>100 then
     raise_application_error(-20011,'Each extraction label must contain 1 to 100 characters.'); end if;
   end loop;
   v_label:=field.get_string('label');
   v_required:=case when field.get_string('applicability')='required' then 1 else 0 end;
   insert into inv_field values(p_id,k,v_label,typ,v_required);
  end loop;
 exception when others then
  rollback to save_catalog;
  raise;
 end;
end;
/


create or replace package inv_ingest authid definer as
 procedure open_batch(p_key varchar2,p_etag varchar2,p_version varchar2,p_id out number,p_generation out number,p_definition out clob);
 procedure resume_batch(p_id number,p_generation number,p_key out varchar2,p_etag out varchar2,p_version out varchar2);
 procedure index_members(p_batch number,p_generation number,p_names clob,p_completed out clob);
 procedure start_member(p_batch number,p_generation number,p_name varchar2,p_engine varchar2,p_run out number);
 procedure save_fields(p_run number,p_values clob);
 procedure fail_member(p_run number,p_code varchar2);
 procedure finish_batch(p_batch number,p_generation number,p_expected number,p_error varchar2 default null);
end;
/
create or replace package body inv_ingest as
 procedure open_batch(p_key varchar2,p_etag varchar2,p_version varchar2,p_id out number,p_generation out number,p_definition out clob) is
 begin
  begin
   select batch_id into p_id from inv_batch where object_key=p_key and ((p_version is not null and source_version=p_version) or (p_version is null and source_version is null and etag=p_etag));
  exception when no_data_found then
   begin
    insert into inv_batch(object_key,etag,source_version,route_prefix,version_id,target_key)
    select p_key,p_etag,p_version,prefix,inv_catalog.current_id(),target_key from inv_route
     where substr(p_key,1,length(prefix))=prefix;
    if sql%rowcount=0 then p_id:=null; return; end if;
   exception when dup_val_on_index then null;
   end;
   select batch_id into p_id from inv_batch where object_key=p_key and ((p_version is not null and source_version=p_version) or (p_version is null and source_version is null and etag=p_etag));
  end;
  if p_id is not null then
   declare v_state varchar2(12); begin
    select state into v_state from inv_batch where batch_id=p_id;
    if v_state<>'RUNNING' then p_id:=null; return; end if;
   end;
  end if;
  select b.generation,t.definition into p_generation,p_definition from inv_batch b
  join inv_template t on t.version_id=b.version_id where b.batch_id=p_id;
 end;
 procedure resume_batch(p_id number,p_generation number,p_key out varchar2,p_etag out varchar2,p_version out varchar2) is
 begin
  select object_key,etag,source_version into p_key,p_etag,p_version from inv_batch
  where batch_id=p_id and generation=p_generation and state='RUNNING';
 end;
 procedure mark_started(p_doc number,p_generation number) is
  pragma autonomous_transaction;
 begin
  merge into inv_doc_checkpoint c using (select document_id from inv_document where document_id=p_doc) s
   on(c.document_id=s.document_id and c.generation=p_generation)
   when not matched then insert(document_id,generation) values(s.document_id,p_generation);
  commit;
 end;
 procedure mark_finished(p_run number) is
 begin
  update inv_doc_checkpoint set finished_at=systimestamp where (document_id,generation) =
   (select document_id,generation from inv_run where run_id=p_run);
 end;
 procedure start_member(p_batch number,p_generation number,p_name varchar2,p_engine varchar2,p_run out number) is
  v_doc number; v_version number; v_existing number;
 begin
  p_run:=null;
  begin
   select version_id into v_version from inv_batch where batch_id=p_batch and generation=p_generation and state='RUNNING';
  exception when no_data_found then return;
  end;
  begin
   select document_id into v_doc from inv_document where batch_id=p_batch and member_name=p_name;
  exception when no_data_found then
   begin
    insert into inv_document(batch_id,version_id,member_name) values(p_batch,v_version,p_name) returning document_id into v_doc;
   exception when dup_val_on_index then
    select document_id into v_doc from inv_document where batch_id=p_batch and member_name=p_name;
   end;
  end;
  begin
   insert into inv_run(document_id,version_id,generation,extractor_version) values(v_doc,v_version,p_generation,p_engine) returning run_id into p_run;
   mark_started(v_doc,p_generation);
  exception when dup_val_on_index then p_run:=null;
  end;
 end;
 procedure index_members(p_batch number,p_generation number,p_names clob,p_completed out clob) is
  v_version number;
 begin
  begin
   select version_id into v_version from inv_batch where batch_id=p_batch and generation=p_generation and state='RUNNING' for update;
  exception when no_data_found then p_completed:=p_names; return;
  end;
  merge into inv_document d using
   (select member_name from json_table(p_names,'$[*]' error on error columns(member_name varchar2(1024 byte) path '$' error on error))) s
   on(d.batch_id=p_batch and d.member_name=s.member_name)
   when not matched then insert(batch_id,version_id,member_name) values(p_batch,v_version,s.member_name);
  update inv_batch set expected_docs=(select count(*) from inv_document where batch_id=p_batch) where batch_id=p_batch;
  select json_arrayagg(d.member_name returning clob) into p_completed from inv_document d join inv_run r on r.document_id=d.document_id
   where d.batch_id=p_batch and r.generation=p_generation;
 end;
 procedure save_fields(p_run number,p_values clob) is
  v_version number; v_expected number;
 begin
  select version_id into v_version from inv_run where run_id=p_run;
  select count(*) into v_expected from inv_field where version_id=v_version;
  insert into inv_result(run_id,version_id,field_key,status,original_text,normalized_text,amount,currency_code,page_no,method,evidence)
  select p_run,v_version,j.k,j.s,j.raw_value,j.val,
    case when f.data_type='currency' and j.s='found' then to_number(j.val,'999999999999999999999999999999D99','NLS_NUMERIC_CHARACTERS=''.,''') end,
    j.ccy,j.pg,j.m,j.ev from json_table(p_values,'$[*]' error on error columns
     (k varchar2(64) path '$.key',s varchar2(12) path '$.status',raw_value varchar2(2000 char) path '$.original' null on empty,
      val varchar2(2000 char) path '$.normalized' null on empty,ccy varchar2(3) path '$.currency' null on empty,
      pg number path '$.page' null on empty,m varchar2(8) path '$.method' null on empty,ev varchar2(250 char) path '$.evidence' null on empty)) j
   join inv_field f on f.field_key=j.k and f.version_id=v_version;
  if sql%rowcount<>v_expected then raise_application_error(-20001,'The extraction result is incomplete.'); end if;
  mark_finished(p_run);
 end;
 procedure fail_member(p_run number,p_code varchar2) is
 begin
  update inv_run set error_code=p_code where run_id=p_run;
  mark_finished(p_run);
 end;
 procedure finish_batch(p_batch number,p_generation number,p_expected number,p_error varchar2 default null) is
  v_count number; v_errors number; v_review number;
 begin
  select count(*),count(r.error_code) into v_count,v_errors from inv_run r join inv_document d on d.document_id=r.document_id
   where d.batch_id=p_batch and r.generation=p_generation;
  select count(*) into v_review from inv_result x join inv_run r on r.run_id=x.run_id join inv_document d on d.document_id=r.document_id
   join inv_field f on f.version_id=x.version_id and f.field_key=x.field_key where d.batch_id=p_batch and r.generation=p_generation
   and (x.status in ('ambiguous','invalid') or (f.required_flag=1 and x.status='absent'));
  update inv_batch set expected_docs=p_expected,error_code=p_error,
   state=case when p_error is not null or p_expected=0 then 'ERROR' when v_count<>p_expected then 'RUNNING'
              when v_errors+v_review>0 then 'REVIEW' else 'DONE' end
   where batch_id=p_batch and generation=p_generation and state='RUNNING';
 end;
end;
/
create or replace package inv_ui authid definer as
 function permitted(p_level varchar2) return number;
 procedure save_template(p_definition clob,p_id out number);
 procedure register_target(p_key varchar2,p_title varchar2);
 procedure set_route(p_prefix varchar2,p_target varchar2,p_previous varchar2 default null);
 procedure approve(p_result number,p_last_review number,p_value varchar2,p_reason varchar2);
 procedure retry_batch(p_batch number);
end;
/
create or replace package body inv_ui as
 function permitted(p_level varchar2) return number is v number;
 begin
  select count(*) into v from inv_operator where username=upper(sys_context('APEX$SESSION','APP_USER'))
   and sys_context('APEX$SESSION','APP_ID')='100'
   and (permission='ADMIN' or permission=p_level); return v;
 end;
 procedure require_permission(p_level varchar2) is
 begin
  if permitted(p_level)=0 then raise_application_error(-20003,'You do not have permission to perform this action.'); end if;
 end;
 procedure save_template(p_definition clob,p_id out number) is
 begin
  require_permission('ADMIN');
  inv_catalog.save_template(p_definition,p_id);
 end;
 procedure register_target(p_key varchar2,p_title varchar2) is
 begin
  require_permission('ADMIN');
  if p_key is null or not regexp_like(p_key,'^[A-Z][A-Z0-9_]{0,63}$','c') or trim(p_title) is null then
   raise_application_error(-20013,'Enter a destination title and a key of up to 64 characters. Start the key with an uppercase letter and use only uppercase letters, digits, or underscores.'); end if;
  merge into inv_target t using(select p_key k from dual) s on(t.target_key=s.k)
   when matched then update set title=trim(p_title) when not matched then insert values(p_key,trim(p_title));
 end;
 procedure set_route(p_prefix varchar2,p_target varchar2,p_previous varchar2 default null) is
  v_root varchar2(512); v_other number; v_catalog number;
 begin
  require_permission('ADMIN');
  select source_prefix into v_root from inv_settings where id=1 for update;
  if p_prefix is null or substr(p_prefix,1,length(v_root))<>v_root or substr(p_prefix,-1)<>'/'
     or instr(p_prefix,'..')>0 or instr(p_prefix,'//')>0 then raise_application_error(-20004,'Enter a folder prefix within the configured source folder.'); end if;
  if p_previous is not null and p_previous<>p_prefix then delete from inv_route where prefix=p_previous; end if;
  select count(*) into v_other from inv_route where prefix<>p_prefix and
   (substr(prefix,1,length(p_prefix))=p_prefix or substr(p_prefix,1,length(prefix))=prefix);
  if v_other>0 then raise_application_error(-20005,'The folder prefix overlaps another route.'); end if;
  v_catalog:=inv_catalog.current_id();
  if v_catalog is null then raise_application_error(-20011,'Save a template before configuring a route.'); end if;
  merge into inv_route t using(select p_prefix prefix from dual) s on(t.prefix=s.prefix)
   when matched then update set version_id=v_catalog,target_key=p_target
   when not matched then insert(prefix,version_id,target_key) values(p_prefix,v_catalog,p_target);
 end;
 procedure approve(p_result number,p_last_review number,p_value varchar2,p_reason varchar2) is
  v_doc number; v_key varchar2(64); v_latest number; v_review number;
 begin
  require_permission('REVIEW');
  if trim(p_value) is null or trim(p_reason) is null then raise_application_error(-20006,'Enter both the corrected value and a reason.'); end if;
  select r.document_id,x.field_key into v_doc,v_key from inv_result x join inv_run r on r.run_id=x.run_id where x.result_id=p_result;
  select document_id into v_doc from inv_document where document_id=v_doc for update;
  select max(x.result_id) keep(dense_rank last order by r.generation), nvl(max(a.review_id),0) into v_latest,v_review
   from inv_result x join inv_run r on r.run_id=x.run_id left join inv_review a on a.result_id=x.result_id
   where r.document_id=v_doc and x.field_key=v_key;
  if p_result<>v_latest or v_review<>nvl(p_last_review,0) then raise_application_error(-20007,'This field has changed. Refresh the page before saving.'); end if;
  insert into inv_review(result_id,approved_value,reason,actor) values(p_result,trim(p_value),trim(p_reason),sys_context('APEX$SESSION','APP_USER'));
 end;
 procedure retry_batch(p_batch number) is
  v_url varchar2(2048); v_generation number; v_payload varchar2(500);
 begin
  require_permission('REVIEW');
  update inv_batch set generation=generation+1,state='RUNNING',error_code=null where batch_id=p_batch and state<>'RUNNING' returning generation into v_generation;
  if sql%rowcount=0 then select generation into v_generation from inv_batch where batch_id=p_batch; end if;
  select invoke_url into v_url from inv_settings where id=1;
  if v_url is null then raise_application_error(-20008,'The OCI Functions endpoint is missing.'); end if;
  select json_object('batch_id' value p_batch,'generation' value v_generation) into v_payload from dual;
  commit;
  execute immediate q'~declare r dbms_cloud_types.resp; begin
   r:=dbms_cloud.send_request(credential_name=>'OCI$RESOURCE_PRINCIPAL',uri=>:url,method=>'POST',
    headers=>'{"Content-Type":"application/json","Fn-Invoke-Type":"detached"}',body=>utl_raw.cast_to_raw(:payload));
   if dbms_cloud.get_response_status_code(r) not between 200 and 299 then raise_application_error(-20009,'The batch retry could not be scheduled.'); end if;
  end;~' using v_url,v_payload;
 end;
end;
/

create or replace view inv_v_batches as
 with samples as (
 select d.batch_id,r.generation,count(*) processed_docs,count(r.error_code) error_docs,
        max(c.finished_at)-min(c.started_at) elapsed
 from inv_document d join inv_run r on r.document_id=d.document_id
 left join inv_doc_checkpoint c on c.document_id=d.document_id and c.generation=r.generation
 group by d.batch_id,r.generation),
 progress as (
 select s.*,extract(day from elapsed)*86400+extract(hour from elapsed)*3600+
   extract(minute from elapsed)*60+extract(second from elapsed) elapsed_seconds from samples s)
 select b.batch_id,b.object_key,b.target_key,b.generation,b.expected_docs,b.state,b.error_code,b.created_at,
 nvl(p.processed_docs,0) processed_docs,nvl(p.error_docs,0) error_docs,
 greatest(0,b.expected_docs-nvl(p.processed_docs,0)) remaining_docs,
 case when b.state<>'RUNNING' then 0 when p.elapsed_seconds>0 and p.processed_docs>0 then
 ceil(p.elapsed_seconds*greatest(0,b.expected_docs-p.processed_docs)/p.processed_docs) end eta_seconds
 from inv_batch b left join progress p on p.batch_id=b.batch_id and p.generation=b.generation where inv_ui.permitted('REVIEW')=1
/
create or replace view inv_v_results as
 with last_result as (
  select d.batch_id,d.document_id,d.member_name,r.generation,x.*,
   row_number() over(partition by d.document_id,x.field_key order by r.generation desc) seq
  from inv_document d join inv_run r on r.document_id=d.document_id join inv_result x on x.run_id=r.run_id
 ), last_review as (
  select r.document_id,x.field_key,a.*,row_number() over(partition by r.document_id,x.field_key order by a.review_id desc) seq
  from inv_review a join inv_result x on x.result_id=a.result_id join inv_run r on r.run_id=x.run_id
 )
 select x.batch_id,x.document_id,x.member_name,x.generation,x.result_id,x.field_key,f.label,x.original_text,x.normalized_text,
 x.page_no,x.method,x.evidence,x.amount,x.currency_code,x.status,a.review_id,a.reason,a.actor,a.approved_value,
 coalesce(a.approved_value,x.normalized_text) effective_value,
 case when a.review_id is not null and a.result_id<>x.result_id and decode(a.approved_value,x.normalized_text,1,0)=0 then 'DISCREPANCIA'
      when a.review_id is not null then 'APROBADO' when x.status in ('ambiguous','invalid') or (x.status='absent' and f.required_flag=1) then 'REVISAR'
      else 'LEIDO' end review_state
 from last_result x join inv_field f on f.version_id=x.version_id and f.field_key=x.field_key
 left join last_review a on a.document_id=x.document_id and a.field_key=x.field_key and a.seq=1
 where x.seq=1 and inv_ui.permitted('REVIEW')=1
/
create or replace view inv_v_templates as
 select t.name,t.definition from inv_template t where t.version_id=inv_catalog.current_id() and inv_ui.permitted('ADMIN')=1
/
create or replace view inv_v_routes as
 select r.prefix,t.name,r.target_key from inv_route r cross join inv_template t
 where t.version_id=inv_catalog.current_id() and inv_ui.permitted('ADMIN')=1
/
create or replace view inv_v_targets as
 select target_key,title from inv_target where inv_ui.permitted('ADMIN')=1
/
create or replace view inv_v_documents as
 select d.batch_id,d.document_id,d.member_name,b.generation,r.error_code,coalesce(c.started_at,r.created_at) created_at,
 case when r.error_code is not null then 'ERROR'
      when exists(select 1 from inv_result x join inv_field f on f.version_id=x.version_id and f.field_key=x.field_key
            where x.run_id=r.run_id and (x.status in ('ambiguous','invalid') or (f.required_flag=1 and x.status='absent'))) then 'REVIEW'
      when r.run_id is not null then 'DONE'
      when c.started_at is not null then 'RUNNING' else 'PENDING' end state,c.finished_at
 from inv_document d join inv_batch b on b.batch_id=d.batch_id
 left join inv_run r on r.document_id=d.document_id and r.generation=b.generation
 left join inv_doc_checkpoint c on c.document_id=d.document_id and c.generation=b.generation
 where inv_ui.permitted('REVIEW')=1
/
