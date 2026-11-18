create table if not exists inv_operator (
 username varchar2(255) primary key, permission varchar2(10) not null check(permission in ('ADMIN','REVIEW')),
 check(username=upper(username))
)
/
create table if not exists inv_settings (
 id number primary key check(id=1), invoke_url varchar2(2048), source_prefix varchar2(512) not null
)
/
create table if not exists inv_template (
 version_id number generated always as identity primary key,
 name varchar2(100) not null, version_no number not null, definition clob not null check(definition is json strict with unique keys),
 created_at timestamp with time zone default systimestamp not null,
 constraint inv_template_version unique(name,version_no)
)
/
create table if not exists inv_field (
 version_id number references inv_template not null, field_key varchar2(64) not null,
 label varchar2(200) not null, data_type varchar2(12) not null check(data_type in ('string','date','currency')),
 required_flag number(1) not null check(required_flag in (0,1)),
 primary key(version_id,field_key)
)
/
create table if not exists inv_target (
 target_key varchar2(64) primary key, title varchar2(200) not null
)
/
create table if not exists inv_route (
 prefix varchar2(512) primary key, version_id number references inv_template not null,
 target_key varchar2(64) references inv_target not null
)
/
create table if not exists inv_batch (
 batch_id number generated always as identity primary key, object_key varchar2(1024 byte) not null,
 etag varchar2(128) not null, source_version varchar2(256), route_prefix varchar2(512) not null,
 target_key varchar2(64) references inv_target not null, version_id number references inv_template not null,
 generation number default 1 not null, expected_docs number, error_code varchar2(80),
 state varchar2(12) default 'RUNNING' not null check(state in ('RUNNING','DONE','REVIEW','ERROR')),
 created_at timestamp with time zone default systimestamp not null,
 unique(batch_id,version_id)
)
/
create unique index if not exists inv_batch_revision_uq on inv_batch
 (object_key,case when source_version is not null then 'v:'||source_version else 'e:'||etag end)
/
create table if not exists inv_document (
 document_id number generated always as identity primary key, batch_id number not null, version_id number not null,
 member_name varchar2(1024 byte) not null, unique(batch_id,member_name), unique(document_id,version_id),
 foreign key(batch_id,version_id) references inv_batch(batch_id,version_id)
)
/
create table if not exists inv_run (
 run_id number generated always as identity primary key, document_id number not null, version_id number not null,
 generation number not null, extractor_version varchar2(80) not null,
 created_at timestamp with time zone default systimestamp not null, error_code varchar2(80),
 unique(document_id,generation), unique(run_id,version_id),
 foreign key(document_id,version_id) references inv_document(document_id,version_id)
)
/
create table if not exists inv_result (
 result_id number generated always as identity primary key, run_id number not null, version_id number not null,
 field_key varchar2(64) not null, status varchar2(12) not null check(status in ('found','absent','ambiguous','invalid')),
 original_text varchar2(2000 char), normalized_text varchar2(2000 char), amount number, currency_code varchar2(3),
 page_no number, method varchar2(8) check(method in ('native','ocr')), evidence varchar2(250 char),
 unique(run_id,field_key), foreign key(run_id,version_id) references inv_run(run_id,version_id),
 foreign key(version_id,field_key) references inv_field(version_id,field_key)
)
/
create table if not exists inv_review (
 review_id number generated always as identity primary key, result_id number references inv_result not null,
 approved_value varchar2(2000 char) not null, reason varchar2(500 char) not null, actor varchar2(255) not null,
 created_at timestamp with time zone default systimestamp not null
)
/
create index if not exists inv_batch_state_ix on inv_batch(state,created_at)
/
create index if not exists inv_review_result_ix on inv_review(result_id,review_id)
/
declare
 n number;
begin
 select count(*) into n from user_tab_columns where table_name='INV_SETTINGS' and column_name='OBJECT_BASE_URL';
 if n=0 then execute immediate 'alter table inv_settings add object_base_url varchar2(2048)'; end if;
end;
/
create table if not exists inv_doc_checkpoint (
 document_id number references inv_document not null, generation number not null,
 started_at timestamp with time zone default systimestamp not null, finished_at timestamp with time zone,
 primary key(document_id,generation)
)
/
create table if not exists inv_pdf_test (
 test_id varchar2(32) primary key, actor varchar2(255) not null,
 version_id number references inv_template not null, filename varchar2(255 char) not null,
 file_data blob, result_json clob check(result_json is json strict), claim_id varchar2(32),
 state varchar2(10) default 'QUEUED' not null check(state in ('QUEUED','RUNNING','DONE','ERROR')),
 stage varchar2(20) default 'QUEUED' not null, error_code varchar2(80),
 created_at timestamp with time zone default systimestamp not null, updated_at timestamp with time zone default systimestamp not null
)
/
create index if not exists inv_test_actor_ix on inv_pdf_test(actor,created_at)
/
create table if not exists inv_test_page (
 test_id varchar2(32) references inv_pdf_test on delete cascade not null,
 page_no number not null check(page_no between 1 and 64),
 width_px number not null check(width_px between 1 and 1280), height_px number not null check(height_px between 1 and 1280),
 picture blob not null, primary key(test_id,page_no)
)
/
declare
 n number;
begin
 select count(*) into n from user_tab_columns where table_name='INV_TEST_PAGE' and column_name='OCR_LINES';
 if n=0 then execute immediate 'alter table inv_test_page add (ocr_total number,ocr_shown number default 0 not null,ocr_lines clob check(ocr_lines is json strict))'; end if;
 select count(*) into n from user_tab_columns where table_name='INV_TEST_PAGE' and column_name='TEXT_METHOD';
 if n=0 then execute immediate q'~alter table inv_test_page add (text_method varchar2(6) check(text_method in ('native','ocr')))~'; end if;
end;
/

