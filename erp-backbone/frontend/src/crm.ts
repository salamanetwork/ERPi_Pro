export type Lead = {id:number; number:string; name:string; company:string; email:string; phone:string; source:string;
  state:string; site_id:number; owner_id:number; owner:string; revision:number; opportunity_id:number|null}
export type Stage = {id:number; code:string; name_en:string; name_ar:string; order:number; kind:'open'|'won'|'lost'; active:boolean; revision:number}
export type Opportunity = {id:number; number:string; title:string; site_id:number; owner_id:number; owner:string; lead_id:number|null;
  stage_id:number; stage:Stage; customer_id:number|null; customer:string|null; has_customer:boolean; currency:string;
  estimated_amount:string; expected_close:string|null; revision:number; quotation:{id:number;number:string;status:string}|null}
export type Activity = {id:number; number:string; site_id:number; owner_id:number; owner:string; kind:string; title:string;
  notes:string; due_at:string|null; state:string; revision:number; parent:string; lead_id:number|null; opportunity_id:number|null}
export type CustomerDraft = {name:string; legal_name:string; contact:string; billing:string; shipping:string; tax_id:string; terms:number}
export type CustomerRequest = {id:number;number:string;name:string; state:string;site_id:number; opportunity_id:number; reason:string;
  requested_by:string;customer_code:string|null;customer_id:number|null;fields:CustomerDraft; can_decide?:boolean;revision:number}
export type History = {id:number; previous:Partial<Record<'ar'|'en',string>>;stage:Record<'ar'|'en',string>;actor:string;created_at:string;reason:string}
export type Page<T> = {count:number;page:number;page_size:number;next:string|null;previous:string|null;results:T[]}
export type Owner = {id:number;username:string}
export type Customer = {id:number;code:string;name:string}
export const blankCustomer = ():CustomerDraft => ({name:'',legal_name:'',contact:'',billing:'',shipping:'',tax_id:'',terms:30})
