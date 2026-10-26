# Part 4 notes (read so far: services dedup,classify,normalize,conflict_resolution,verification,listing_diff,requirement_satisfaction; workflows url_match,app_plan,submit_prep; tools all 17)
DD: dedup.py:86-90 embeds candidate + every existing row each call (N+1 embed calls, no cache); :57 zip strict raises on dim mismatch; key exact lower/strip only.
NZ: normalize.py:52,62 explicit_status honoured even when contradicts value (known+None / unknown+value); present values default known/inferred.
CR: conflict_resolution.py:40 repr() distinctness; None from silent source counts as disagreement; :61 naive/aware datetime TypeError; official never True (IG-02).
VF: verification.py:17 180 hard-coded; :56,:60 naive vs aware; :53-54 official-confirmed first time -> NEWLY_DISCOVERED; :60 UPDATED when retrieved>verified regardless of change.
LD: listing_diff.py:38 datetime string -> fromisoformat ValueError -> deadline silently dropped; removal not detected.
RS: requirement_satisfaction.py:70 float(value) crash on text; :75 only doc normalized to 4.0, req assumed 4.0 (comment :72 claims more); :94 vs :104 dead branch (value None -> unknown before); :97 key must equal test name; extract prompt puts IELTS under "language" which has no evaluator (:148); :133 substring degree compare gives NOT_SATISFIED on apostrophe/ordering (violates 'unknown not collapse'); :181-190 profile inconsistency ignores gpa_scale, exact institution string, retake false positive.
UM: url_match/graph.py:72 AI extract before :80 existing check (wasted call for stored URL); :94 datetime string lost; :192-202 official_source=user URL; :149 new Match row each call (check match_repo); :95 UNVERIFIED but :105 AI confidence passthrough.
AP: app_plan/graph.py:71 sorts by description not key (docstring :17-20 says key); replace_tasks -> check task status loss.
SP: submit_prep/graph.py:94-113 fingerprint docs only; :178-196 staged = ids/types only; check scope validation.
ER: extract_requirements.py:60-72 funding fields (tuition_amount_or_pct, family_dependent_benefits, dependent_policy) not in prompt shape :35-45; :32 'verified' undefined in prompt; :91 [:20000] silent trunc; :93-94 no validation retry; category list has language+test.
EL: extract_listing.py:105 strips hrefs => detail_url invented (detect_candidate_links.py:5-8 admits); :141 only name grounded, deadline/funding unchecked; :112 20000 trunc but ground vs full; :130 list JSON -> AttributeError.
DF: extract_document_fields.py:66 [:20000] cuts last-page cumulative GPA; no grounding of values vs text; 
EP: extract_profile.py:23 ValidationError raw.
CL: tools/classify.py:48 'not fully funded' -> fully_funded; :66-72 substring country ('usa' in 'thousand','uk' in 'duke'); :74-79 field 'arts' in 'starts','law' in 'flaw'; :43 first-match order; :145 LLM value unvalidated.
GC: ground_check.py no negation; number regex.
PP: pdf_parse.py:36 is_encrypted vs needs_pass (owner-only PDFs rejected?) -> test; find_tables on every page.
WF: web_fetch.py:10 example.invalid UA; no size cap :51; no content-type; redirects :30; InvalidURL not caught.
SE: search.py:56 netloc exact; RR rerank off.
