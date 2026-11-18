declare
 v_id number; v_prefix varchar2(512); v_count number;
begin
 begin
  select version_id into v_id from inv_template where name=json_value(:catalog,'$.name') and version_no=json_value(:catalog,'$.version' returning number);
 exception when no_data_found then
  inv_catalog.install_version(:catalog,v_id);
 end;
 merge into inv_target t using(select 'FACTURAS' k from dual) s on(t.target_key=s.k)
  when not matched then insert values(s.k,'Invoices');
 select source_prefix into v_prefix from inv_settings where id=1;
 select count(*) into v_count from inv_route;
 if v_count=0 then insert into inv_route values(v_prefix,v_id,'FACTURAS'); end if;
end;
/
