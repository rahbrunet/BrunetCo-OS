# Discovery Findings — AppColl Live Review, SharePoint Crawl, CSV Export Analysis

This file documents three separate, sequential rounds of directly inspecting the firm's real, live systems and data — as opposed to the interview process, which relied on the owner's description of how things work. All three rounds surfaced things the interview alone hadn't, which is exactly why they were worth doing. This is the evidentiary backbone behind decisions D35, D36, and D37.

**A note on security practice, since it came up directly during this project:** at one point the owner offered his AppColl username and password directly in chat, intending for Claude to log in. Claude does not accept or use credentials under any circumstances — this is a hard rule, not a judgment call about trustworthiness. The owner was asked to rotate the password immediately (since it had been transmitted in plaintext), which he confirmed doing, and the actual review was instead conducted through the **Claude in Chrome extension**, with the owner logged into his own authenticated browser session. This pattern — owner holds the session, Claude reviews what's already authenticated — was used for both the AppColl review and the SharePoint crawl below.

---

## Round 1: AppColl In-App Review

Conducted via the owner's authenticated Chrome session (browser deviceId `56c2ae9e-bc4f-4444-b0a1-1f37d850dbae`, on `s2.appcoll.com`). The review covered four modules before the session's scope was used up: **Tasks**, **Matters** (list and one full detail record), and **Billing**. Prior Art, Files, Reports, Contacts, and Settings were **not** reached in this session — worth noting explicitly, since a follow-up walkthrough of those remaining modules is still a live option (it's listed as an optional action item in the tracker).

### Tasks module

A due-date list showing 838 open trademark tasks was the entry point. Key structural findings:

- **Dual dates per task:** a `RespondBy` date and a separate `FinalDueDate`, plus trigger `RefDate` and `ClosedOn` fields — richer than a single "due date" field would suggest.
- **DeadlineType** field present directly on tasks, later confirmed by the CSV export (Round 3) to have six actual values in use, not the four initially visible.
- **Task statuses** include Received, Not Needed, and Missed, in addition to the obvious Open/Completed — "Not Needed" in particular is a status the interview never surfaced, and it matters (a task that turned out not to be required shouldn't read the same as one that was simply skipped).
- **"Expect to Receive X" tasks** — a distinct category of awaiting-office task (e.g., "Expect to Receive First Examiner's Report") that behaves differently from both a hard deadline and an awaiting-client reminder. This directly produced the M1-R12 requirement for a third first-class "waiting" category in the new OS.
- **Role-based task owners** — "Trademarks Admin" and "Patents Admin" appeared as actual assignees, confirming that role-queue assignment (as opposed to only named-individual assignment) is a real, current practice, not a hypothetical.
- **Comment logs** on tasks are dated and initialed, functioning as an informal activity history — these need to migrate into the new system's structured activity records rather than being dropped, since they represent real institutional history about how each task was actually handled.

### Matters module

The list view showed the full matter count: **3,661 matters**. One detail record (BRZ-0001-CA, a trademark matter for client 3DBioFibr... actually for client BRZ, matter plid 6276973/id 5094) was opened and its complete field set extracted. This single record was the single richest data point in the entire discovery process — it directly produced the bulk of decision D35's field-carry-list:

- **Three-way reference mapping:** the firm's own reference, the client's own reference, *and* — critically — a **Foreign Associate Reference** field. The interview process had never surfaced that foreign associate references get tracked as their own field distinct from the firm's and client's.
- **Role assignments per matter:** Paralegal, Attorney, Client Contact, and Law Firm are all separate fields on the matter itself, not just implied by who happens to be working on it.
- **US trademark-specific fields:** a **Basis** field supporting 1(a), 1(b), 44(d), 44(e), 66(a), and combinations thereof; a **Register** field (Principal/Supplemental); and a mark-image field.
- **Fast Track** program flag, **Examiner/Art Unit/Classification** fields, **Terminal Disclaimer** tracking, **Reel/Frame** (for recorded assignments), a **US Government agency/contract** field, and a **WIPO DAS code** field.
- **Complexity rating** (Low/Medium/High) — explicitly earmarked in the new spec to feed the workload-based assignment engine (M9), since a "High" complexity matter shouldn't be assigned the same way as a routine renewal.
- **Products/Keywords/Technologies tags**, full goods-and-services text, per-matter **Fees/Expenses Caps**, and an "invoice only when filed" flag.
- **Typed Connections** to external priority applications — the concrete example found was a link to an Italian priority filing (`IT 302023000119544`) that was never itself a Brunet & Co. matter. This is a genuinely useful pattern: it means the reference/relationship model needs to accommodate links to applications *outside* the firm's own portfolio, not just parent-child relationships between the firm's own matters.
- **Matter status vocabulary**, confirmed directly from the status dropdown: Unfiled, Missing Parts, Pending, Published, Allowed, Issued, Expired, Abandoned, **"Cient Abandoned"** (a typo in the live system — the OS corrects this to a properly-spelled "Client Abandoned" enum value, while preserving the *distinction* it represents, since a matter abandoned by office action and one abandoned by client decision are meaningfully different things), Inactive, Closed, Transferred, and Reinstatable.
- **Matter types** include non-registered/advisory categories — Contract, Disclosure, Other — confirming that advisory work genuinely lives inside the matter data model as its own type, not as a workaround.

### Billing module

The uninvoiced billing items view showed **33,756 total billing items** in the system (later CSV analysis in Round 3 refined this to an exact 33,760, likely reflecting items created between the two review sessions), with 194 currently uninvoiced items representing approximately $84,500 in work-in-progress. Notable findings:

- **Billing types observed:** Fee, Flat Fee, Expense, and Retainer-as-line-item — the last of these confirming that retainer transactions currently get recorded as ordinary billing line items in AppColl, which the new OS deliberately does *not* replicate, instead routing all retainer/trust movement through Xero and the virtual trust module (M2-R7).
- **Live evidence of the exact problem M3 is designed to solve:** an expense item was found sitting with a **blank CAD amount** while its description read "380.00 AUD" — a foreign-currency amount typed into a text field, awaiting manual FX conversion and entry. This is direct, concrete evidence for why M3's "expected amount now, actual settled amount and FX rate written back later" design exists, rather than that design being speculative.
- **Associate invoices** (fees billed by foreign associates, which the firm then re-bills to clients) currently get entered as expenses with the associate's invoice details embedded in a free-text description field, rather than as structured data — directly motivating M3's requirement for a proper structured associate-invoice model (associate identity, invoice number, original currency and amount, all as real fields).
- **Client credit / "banked time" programs** — the concrete example found was IPON (a Canadian government IP-support funding program), where a client's billing draws against a pre-funded credit balance rather than being invoiced normally. This produced the new M2-R9 requirement.
- **Zero-amount courtesy items** exist in the live data, where the standard rate is still shown but the actual charge is $0 — a "professional courtesy, but let's keep the number honest for internal tracking" pattern worth preserving.

### Confirmed features to re-imagine, not copy (feeding D33)

The review confirmed several AppColl features are genuinely useful in function even though their specific UI shouldn't be copied: a Matter Family Diagram (visualizing the parent-child structure of a family), a "Regenerate Tasks" function, matter-embedded task views, bulk operations (reassign owner, set contacts across multiple matters at once), saved views that convert into reports, per-view color coding, a global search bar, per-record History/Discussion logs, a soft-delete Trash (rather than permanent deletion), and an in-header "Time Manager" quick-entry tool.

---

## Round 2: SharePoint Tenant Crawl (D36)

Conducted through the same owner-authenticated-browser pattern. One technical note worth recording: the Microsoft 365 connector's own tools (which would have allowed direct Graph API access to SharePoint) **never became available in this conversation**, despite being listed as an available connector for the account — likely because that connector needs to be explicitly enabled per-conversation and wasn't for this session. The crawl was performed successfully anyway via the browser route, but this is flagged as something worth checking before the next session that needs SharePoint access, since the Graph-based route will matter for the actual M5 module build.

The crawl itself: tenant `brunetco365.sharepoint.com`, navigating from the SharePoint home through to the root site "Brunet & Co. Sharepoint," into the document library "Brunet & Co.," into the top folder "Brunet + Co," then drilling all the way down through an actual client (3DB) → an actual family (`0001 - Polymer Strand`) → an actual jurisdiction folder (`US (Issued)`) → the `FH` file-history folder → real filenames.

This confirmed the Appendix A directory structure essentially as specced from the interview, while adding the enrichments documented in full in `07-appendix-A-file-conventions.md`: the `9999 - General` and `Account` per-client folders, the family-level `PriorArt/` folders and per-IDS bundle folders (a ready-made migration seed for the new M11 module), the discovery that folder names currently duplicate docket status (leading to the D36 design ruling, since confirmed by the owner, that the OS stores status only in the database going forward), associates-as-clients (dual-role organizations, directly feeding the A16 reciprocity guard), and the initials-suffix and Unicode-character findings in filenames and folder names.

---

## Round 3: AppColl CSV Export Analysis (D37)

Three files, uploaded by the owner after running exports from AppColl's own Settings and module screens: `AppCollTaskTypesAll20260707.csv`, `AppCollContactsAll20260707.csv`, and `AppCollBillingAll20260707.csv`. This was the first look at the system's actual *configured behavior*, as opposed to its screens — and it turned out to be the richest single data source in the whole project, changing the M1 rule-engine design in a genuine, structural way, not just adding detail.

### Task Types (552 rows)

The headline finding: **the DeadlineType taxonomy has six values in actual use, not the four visible in the UI review (Round 1).** The exact distribution: Event (270 rules — by far the most common), Hard External Deadline (151), Extendable External Deadline (65), Internal Deadline (35), General Reminder (28), and Transient Event (3, a small but real category). This directly amended M1-R11's DeadlineType enum from four values to six.

**Owner-resolution vocabulary** turned out to be more sophisticated than "assign to a role": 365 of the 552 rules resolve their owner as "Attorney for matter" — a *matter-relative* resolution, not a fixed queue — with the remainder split across genuinely fixed role queues (Patents Admin, Trademarks Admin, Account administrator), a "Current User" resolution, and even multi-assignee expansion via "All inventors for matter" (used at least once, for a rule that presumably needs every inventor notified). This became M1-R13.

**Rule actions beyond task creation:** at least two rules in the export carry an action string like `Update Matter: AllowanceDate={TriggeringTask.RefDate}` — meaning some legacy rules don't just spawn a follow-on task, they directly **write a field on the matter itself**, using a template expression evaluated against the triggering task or event. This is a real capability the new rule engine needs to replicate, not an edge case to ignore.

**Reminder ladders as rule pairs:** examining the CA-TM renewal rules specifically showed the pattern directly — "CA-TM-Client Reminder Pay TM Renewal (First)" (offset 108 months) and "CA-TM-Pay 10 Year Renewal Fee" (offset 120 months) exist as two *separate* task-type rules today, effectively implementing a two-step reminder ladder as two independent tasks. In the new OS, this pattern migrates into a proper A18 ladder *definition* rather than surviving as multiple standalone task types — the same underlying business logic, expressed more cleanly.

**Other findings:** 299 of the 552 rules have auto-generation enabled; roughly 65 task types exist purely to support AppColl's own USPTO integration (these are superseded outright by Agent A1's notification-driven design and won't need migrating as rules per se); 20 rules define alternate offset paths (conditional dual deadlines); and no rules use an "end of month" offset convention (a possibility that was checked and ruled out). **One honest limitation:** the trigger-event linkage between rules — i.e., which rule actually fires which other rule — is stored as an opaque internal ID in the export, not as a readable reference. Reconstructing that wiring per family will need to happen during the WP 1.3 golden-test process against live matter data, not from the CSV alone.

### Contacts (4,596 rows)

Role vocabulary, by frequency: Inventor (2,715 — by far the largest group), Company (915), Client (482), Employee (191), **Foreign Associate (176)** — confirming the dual-role-organization pattern found independently in the SharePoint crawl — Client/Solo Inventor (34, a hybrid role), Assignee (27), Paralegal (15), Law Firm (8), Associate (6), Patent Attorney (6), and a handful of others.

1,346 contacts carry **citizenship** data, and a meaningful number carry separate **residence** addresses distinct from their business address — both are inputs the firm actually needs for USPTO declarations and powers of attorney, confirming M7's requirement to carry both address types rather than assuming one address per contact is sufficient. 47 contacts have recorded **aliases**. Only 3 contacts have `DisableReminders` set — a small number, but its *existence* as a field is what matters: it's the direct precedent for Agent A18's per-contact suppression capability. Wire-transfer instructions exist on only 7 contacts (a small, sensitive dataset worth handling carefully in migration).

### Billing (33,760 rows)

The full billing-type vocabulary, well beyond what the in-app Round 1 review alone had shown: Flat Fee (10,874 — the largest category), **Payment from Client (8,722)**, Expense (8,051), Fee (5,616), Payment from Retainer (262), Retainer (205), Retainer Refund (12), Fee Adjustment (11), Expense Adjustment (5), Write-Off (1), and Retainer Withdrawal (1).

The architecturally significant finding here: the payment, retainer, and refund/withdrawal types together represent **money movement**, not billable work — and per the M2 module design, none of these get replicated as OS billing items at all. They map instead to Xero and the virtual trust module during migration, which is exactly the boundary the raw data made obvious in a way that browsing the AppColl UI hadn't fully clarified. This became explicit in M2-R10.

Other findings: item-level **Adjustments** were used 1,738 times; the **TaxableItem** flag was set on 17,224 of the 33,760 items (roughly half), confirming this flag genuinely drives real HST computation in current practice rather than being an unused field; **activity codes** are in heavy live production use — PA430 alone appears on 4,598 items, with PA130, PA400, PA630, PA730, TR100, PA120, PA600, PA510, and PA420 all appearing hundreds of times each — motivating the optional activity-code taxonomy added as part of M2-R10; and the structured **Vendor/VendorInvoice** fields that exist in the AppColl schema are used only about 90 times each, against thousands of associate invoices that instead get embedded as free text in item descriptions — direct, hard confirmation that M3's structured associate-invoice redesign fixes a real, widespread habit rather than a hypothetical inefficiency. Finally, **8,756 distinct invoice numbers** appear across the dataset, giving a real sense of invoicing cadence and volume for capacity planning.
