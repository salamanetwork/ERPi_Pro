# ERPi Pro — Technical Checkpoint (`v2.11.0`)
**Timestamp:** 2026-11-28 | **Git Tag:** `v2.11.0` | **Branch:** `master`

## 1. Verification Matrix
| Subsystem | Command / Check | Status | Metrics / Notes |
| :--- | :--- | :--- | :--- |
| **Module Contracts** | `python manage.py audit_modules` | PASS | 29 contracts passed (`RELEASE_VERSION = "2.11.0"`) |
| **Django System** | `python manage.py check` | PASS | 0 issues silenced; migration `0023` applied |
| **i18n Integrity** | `npm run check:i18n` | PASS | 1,988 paired keys across 9 locales; 77 help topics; 259 field explanations |
| **Print i18n & Identity** | `test_company_profile.py` / `test_document_services.py` | PASS | 9-locale print templates (`RTL`/`LTR`) + TRN, CR, currency, address |
| **Frontend Components** | `frontend/src/*.vue` | VERIFIED | 88 active Vue views & components |

## 2. Active Backend Django Apps & Contracts (`29`)
- **Core & Native Manifests (`15`):** `platform`, `accounting`, `assets`, `crm`, `hr`, `inventory`, `purchasing`, `sales`, `supply`, `workorders`, `suppliers`, `purchase_orders`, `supplier_invoices`, `customers`, `payroll`.
- **Extension Apps (`14`):** `manufacturing`, `projects`, `helpdesk`, `marketing`, `publication`, `public_identity`, `media_service`, `bank_reconciliation`, `billing_core`, `expenses`, `parties`, `pos`, `scheduling`, `subscriptions`.

## 3. Active Frontend Vue Views & Components (88)
- `AccessPage.vue`
- `AccountingPage.vue`
- `App.vue`
- `AppFooter.vue`
- `ApprovalTemplatesPage.vue`
- `AssetDetailPage.vue`
- `AssetsPage.vue`
- `AttendanceCorrections.vue`
- `AttendancePage.vue`
- `AuditPage.vue`
- `BudgetingPage.vue`
- `BusinessChoice.vue`
- `BusinessWorkspace.vue`
- `CommandPalette.vue`
- `CompanyProfilePage.vue`
- `ContextHelp.vue`
- `CopyId.vue`
- `CrmActivityForm.vue`
- `CrmCustomerForm.vue`
- `CrmKanban.vue`
- `CrmLeadPage.vue`
- `CrmOpportunityPage.vue`
- `CrmPage.vue`
- `CrmQuotationForm.vue`
- `CrmSettings.vue`
- `CsvExport.vue`
- `DashboardChart.vue`
- `DataImportPage.vue`
- `DataTable.vue`
- `DateInput.vue`
- `DelegationsPage.vue`
- `DirectoryPage.vue`
- `DocumentSettingsPage.vue`
- `DocumentTools.vue`
- `EmployeeList.vue`
- `EmployeeNumbering.vue`
- `EmployeePersonal.vue`
- `EntityAutocomplete.vue`
- `ExpensePage.vue`
- `FieldHint.vue`
- `FoundationPage.vue`
- `GatewayPage.vue`
- `GroupControls.vue`
- `HelpdeskPage.vue`
- `HistoryTable.vue`
- `HrPage.vue`
- `IamAssignments.vue`
- `InboxPage.vue`
- `InventoryPage.vue`
- `LinkedUserSelect.vue`
- `MaintenancePage.vue`
- `ManufacturingPage.vue`
- `MarketingPage.vue`
- `MatchText.vue`
- `ModuleCatalog.vue`
- `ModuleVisibilityPage.vue`
- `NavigationSettings.vue`
- `NotificationsPage.vue`
- `OffboardingPage.vue`
- `OrdersPage.vue`
- `PageGuidance.vue`
- `PanelResizer.vue`
- `PayablesPage.vue`
- `ProfilePage.vue`
- `ProjectTimesheet.vue`
- `ProjectsPage.vue`
- `PublicationEditor.vue`
- `PublicationMenuRows.vue`
- `PurchaseOrderPage.vue`
- `PurchasePage.vue`
- `PurchaseTemplatesPage.vue`
- `ReferenceSelect.vue`
- `ReportsPage.vue`
- `SalesPage.vue`
- `SavedViews.vue`
- `SearchPage.vue`
- `SecurityHealth.vue`
- `SidebarNavigation.vue`
- `SitesPage.vue`
- `SupplyPage.vue`
- `TableToolbar.vue`
- `TrainingCenter.vue`
- `TrashRestorePage.vue`
- `UnifiedNoticeHost.vue`
- `UsersPage.vue`
- `WorkOrderMaintenance.vue`
- `WorkOrderTime.vue`
- `WorkspacePage.vue`
