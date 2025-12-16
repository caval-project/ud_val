@app.route('/translit', methods=['GET'])
def translit():
    sort_order = request.args.get('sort', 'alphabetical')
    order_direction = request.args.get('order', 'asc')
    if request.args.get('reset') == '1':
        for k in (
            't_initial',
            'selected_verb', 'selected_verb_gloss',
            'translit_search_query', 'english_search_query',
        ):
            session.pop(k, None)
        return redirect(url_for('translit'))
    
    initial_param = request.args.get('initial')
    if initial_param is not None:          # present -> write-through ('' clears)
        session['t_initial'] = initial_param
    
    initial_letter = session.get('t_initial', '')

    selected_verb = request.args.get('selected_verb')
    translit_search_query = request.args.get('translit_search_query', '')
    english_search_query = request.args.get('english_search_query', '')
    selected_verb_gloss = request.args.get('selected_verb_gloss', '')
    # Detect if the side-panel checkboxes have been submitted
    #source_submitted = 'source_checkbox_submitted' in request.args
    selected_sources = request.args.getlist('selected_source')
    
    if translit_search_query.strip() or english_search_query.strip():
        initial_letter = ''
        session['t_initial'] = ''
    # ─── verb‑feature picks ───
    selected_verbforms  = request.args.getlist('verbform')
    selected_aspects    = request.args.getlist('aspect')
    selected_cases      = request.args.getlist('case_feature')
    selected_connegatives = request.args.getlist('Negation')
    selected_moods      = request.args.getlist('mood')
    selected_numbers    = request.args.getlist('number')
    selected_persons    = request.args.getlist('person')
    selected_tenses     = request.args.getlist('tense')
    selected_voices     = request.args.getlist('voice')


    # Add this flag to indicate the transliterated page
    is_translit_page = True
    
    # ── Pagination (sentences) ─────────────────────────────────────────
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    try:
        per_page = max(1, min(200, int(request.args.get('per_page', 50))))  # cap for safety
    except ValueError:
        per_page = 50
    #offset = (page - 1) * per_page

    # --- Customizable language helpers ---
    # language -> translit map (exact order; we only map single chars sequentially)
    ARM_TO_TR = {
        'ա': 'a', 'բ': 'b', 'գ': 'g', 'դ': 'd', 'ե': 'e', 'զ': 'z', 'է': 'ē',
        'ը': 'ə', 'թ': 'tʻ', 'ժ': 'ž', 'ի': 'i', 'լ': 'l', 'խ': 'x', 'ց': 'cʻ', 'ծ': 'c', 'ք': 'kʻ', 'կ': 'k', 'հ': 'h',
        'ձ': 'j', 'ղ': 'ł', 'չ': 'čʻ', 'ճ': 'č', 'մ': 'm', 'յ': 'y', 'ն': 'n', 'շ': 'š', 'ո': 'o', 'փ': 'pʻ', 'պ': 'p',
        'ջ': 'ǰ', 'ռ': 'ṙ', 'ս': 's', 'վ': 'v', 'տ': 't', 'ր': 'r', 'ւ': 'w', 'ֆ': 'f'
    }
    
    def _has_language(s: str) -> bool:
        # Basic range check for language (Ա–Ֆ + ա–ֆ + punctuation range guard)
        return any('\u0531' <= ch <= '\u058F' for ch in s)
    
    def language_to_translit_query(s: str) -> str:
        # lowercased, then map char-by-char with the exact table above
        s = (s or '').lower()
        return ''.join(ARM_TO_TR.get(ch, ch) for ch in s)

    
    def _nz(s):
        """None or zero-length/whitespace-only → None; else stripped string."""
        if s is None:
            return None
        s = s.strip()
        return s or None

    def build_sources_condition(selected_sources, alias="sent_id"):
        if not selected_sources:
            return None
    
        or_conditions = []
        for src in selected_sources:
            if src == "German":
                or_conditions.append(f"{alias} LIKE '%hdt%'")
            elif src == "Dutch":
                or_conditions.append(f"({alias} LIKE '%wiki%' OR {alias} LIKE '%WR-P-E-I%')")
            elif src == "French":
                or_conditions.append(f"{alias} LIKE '%fr%'")
            elif src == "English":
                or_conditions.append(f"{alias} LIKE '%GUM%'")
            elif src == "Greek":
                or_conditions.append(f"{alias} REGEXP '^[0-9]{{5}}$'")
            elif src == "Arabic":
                or_conditions.append(
                    "("
                    f"{alias} NOT LIKE '%hdt%' AND "
                    f"{alias} NOT LIKE '%wiki%' AND {alias} NOT LIKE '%WR-P-E-I%' AND "
                    f"{alias} NOT LIKE '%fr%' AND "
                    f"{alias} NOT LIKE '%GUM%' AND "
                    f"{alias} NOT REGEXP '^[0-9]{{5}}$'"
                    ")"
                )
    
        if not or_conditions:
            return None
    
        return "(" + " OR ".join(or_conditions) + ")"
  
    selected_verb_url = None
    if selected_verb:
        if selected_verb_gloss:
            row = db.session.execute(
                text("""
                    SELECT url
                    FROM verbs
                    WHERE translit_verb = :v COLLATE utf8mb4_bin
                      AND gloss = :g
                    LIMIT 1
                """),
                {'v': selected_verb, 'g': selected_verb_gloss}
            ).fetchone()
            if row:
                selected_verb_url = row.url
        else:
            pass

    def build_feature_conditions_for_alias(conditions, params):
        if selected_verbforms:
            conditions.append("v.VerbForm IN :verbforms_list")
            params["verbforms_list"] = tuple(selected_verbforms)
        if selected_aspects:
            conditions.append("v.Aspect IN :aspects_list")
            params["aspects_list"] = tuple(selected_aspects)
        if selected_cases:
            conditions.append("v.Case IN :cases_list")
            params["cases_list"] = tuple(selected_cases)
        if selected_connegatives:
            conditions.append("v.Connegative IN :conneg_list")
            params["conneg_list"] = tuple(selected_connegatives)
        if selected_moods:
            conditions.append("v.Mood IN :moods_list")
            params["moods_list"] = tuple(selected_moods)
        if selected_numbers:
            conditions.append("v.Number IN :numbers_list")
            params["numbers_list"] = tuple(selected_numbers)
        if selected_persons:
            conditions.append("v.Person IN :persons_list")
            params["persons_list"] = tuple(selected_persons)
        if selected_tenses:
            conditions.append("v.Tense IN :tenses_list")
            params["tenses_list"] = tuple(selected_tenses)
        if selected_voices:
            conditions.append("v.Voice IN :voices_list")
            params["voices_list"] = tuple(selected_voices)

    # --- Customizable (language dependent) list of initial letters in the correct order
    initial_letters_list = [
        'a', 'b', 'g', 'd', 'e', 'z', 'ē', 'ǝ', 'tʻ', 'ž', 'i', 'l', 'x', 'c', 'k', 'h', 'j', 'ł',
        'č', 'm', 'y', 'n', 'š', 'o', 'čʻ', 'p', 'ǰ', 'ṙ', 's', 'v', 't', 'r', 'cʻ', 'w', 'pʻ',
        'kʻ', 'f'
    ]

    # Mapping for transliterated characters
    latin_to_translit = {
        'e=': 'ē', "e'": 'ə', "t'": 'tʻ', 'z=': 'ž', 'l=': 'ł', 'c=': 'č',
        's=': 'š', "c='": 'čʻ', 'j=': 'ǰ', 'r=': 'ṙ', "c'": 'cʻ', "p'": 'pʻ',
        "k'": 'kʻ', 'aw': 'aw', 'a': 'a', 'b': 'b', 'g': 'g', 'd': 'd', 'e': 'e',
        'z': 'z', 'i': 'i', 'l': 'l', 'x': 'x', 'c': 'c', 'k': 'k', 'h': 'h',
        'j': 'j', 'm': 'm', 'y': 'y', 'n': 'n', 'o': 'o', 'p': 'p', 's': 's',
        'v': 'v', 't': 't', 'r': 'r', 'w': 'w', 'f': 'f'
    }
    
    # map translit initial → language single‐char
    initial_map_translit_to_language = {
        'a':'ա',  'b':'բ',  'g':'գ',  'd':'դ',  'e':'ե',  'z':'զ',
        'ē':'է',  'ǝ':'ը', 'tʻ':'թ', 'ž':'ժ',  'i':'ի',  'l':'լ',
        'x':'խ',  'c':'ծ',  'k':'կ',  'h':'հ',  'j':'ձ',  'ł':'ղ',
        'č':'ճ', 'm':'մ', 'y':'յ', 'n':'ն', 'š':'շ', 'o':'ո', 'čʻ':'չ', 'ǰ':'ջ', 
        'ṙ':'ռ',  'cʻ':'ց',
        'p':'պ',  'v':'վ',  't':'տ',  'r':'ր',  'w':'ւ',  'pʻ':'փ',
        'kʻ':'ք', 'f':'ֆ', 'aw':'աւ'
    }

    def convert_latinized_to_translit(input_str):
        # List of keys sorted by length descending
        mapping_keys = sorted(latin_to_translit.keys(), key=lambda x: -len(x))
        output_str = ''
        i = 0
        while i < len(input_str):
            matched = False
            for key in mapping_keys:
                if input_str[i:i+len(key)] == key:
                    output_str += latin_to_translit[key]
                    i += len(key)
                    matched = True
                    break
            if not matched:
                output_str += input_str[i]
                i += 1
        return output_str


    # Build dependencies list
    dependencies = []
    max_dependencies = 5  
    dependency_visible_flags = []
    
    for idx in range(max_dependencies):
        if idx == 0:
            dep = {
                'deprel': request.args.get('syntactic_relation') or None,
                'case_value': request.args.get('case_value') or None,
                # ⬇️ the only change here: normalize translit_lemma
                'lemma': _nz(request.args.get('translit_lemma')),
            }
            visible = True
        else:
            dep = {
                'deprel': request.args.get(f'co_occurring_deprel_{idx+1}') or None,
                'case_value': request.args.get(f'co_occurring_case_value_{idx+1}') or None,
                # ⬇️ and normalize co-occurring lemmas too
                'lemma': _nz(request.args.get(f'co_occurring_lemma_{idx+1}')),
            }
            visible = request.args.get(f'dependency{idx+1}_visible', 'false') == 'true'
    
        dependency_visible_flags.append(visible)
        dependencies.append(dep)


    # Determine the last visible dependency index
    last_visible_dependency_index = -1
    for idx, visible in enumerate(dependency_visible_flags):
        if visible:
            last_visible_dependency_index = idx
            
    # Determine if each dependency has at least one selection
    dependency_has_selection = []
    for dep in dependencies:
        has_selection = any([dep['deprel'], dep['case_value'], dep['lemma']])
        dependency_has_selection.append(has_selection)

    # Define the desired order of dependency relations
    desired_deprel_order = [
        'nsubj', 'nsubj:pass', 'nsubj:caus',
        'csubj', 'csubj:caus', 'csubj:pass', 'obj', 'ccomp',
        'iobj', 'obl', 'obl:agent', 'obl:arg', 'aux', 'aux:caus'
    ]

    # Function to sort deprels
    def sort_deprels(deprels, desired_order):
        desired_set = set(desired_order)
        in_order = [deprel for deprel in desired_order if deprel in deprels]
        not_in_order = sorted([deprel for deprel in deprels if deprel not in desired_set])
        return in_order + not_in_order

    # Function to extract initial letters
    def extract_initial_letter(word):
        matched_initials = [initial for initial in initial_letters_list if word.startswith(initial)]
        if matched_initials:
            # Return the longest matching initial
            return max(matched_initials, key=len)
        return ''

    def get_initials_under_filters_translit(
        dependencies,
        selected_sources,
        initial_letter=None,
        *,
        include_selected_verb=True,
        include_search_filters=True
    ):
        conditions, params, joins = [], {}, ""
    
        # active dependencies
        active = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep = {**dep, 'idx': idx}
                active.append(dep)
                a = f"a{idx}"
                joins += f"JOIN arguments {a} ON verbs.token_id = {a}.head_id AND verbs.sent_id = {a}.sent_id\n"
                if dep['deprel']:
                    conditions.append(f"{a}.dep_rel = :deprel{idx}")
                    params[f"deprel{idx}"] = dep['deprel']
                if dep['case_value']:
                    conditions.append(f"{a}.translit_dep_lemma = :case_value{idx}")
                    params[f"case_value{idx}"] = dep['case_value']
                if dep['lemma']:
                    conditions.append(f"{a}.translit_lemma = :lemma{idx}")
                    params[f"lemma{idx}"] = dep['lemma']
    
        # ensure distinct arguments when multiple deps
        if len(active) >= 2:
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    conditions.append(f"a{active[i]['idx']}.token_id != a{active[j]['idx']}.token_id")
    
        # multi-source
        src_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if src_filter:
            conditions.append(src_filter)
    
        if initial_letter:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin LIKE :__init")
            params["__init"] = f"{initial_letter}%"
            conflicting = [oi for oi in initial_letters_list if oi != initial_letter and oi.startswith(initial_letter)]
            for k, ci in enumerate(conflicting):
                conditions.append(f"verbs.translit_verb COLLATE utf8mb4_bin NOT LIKE :__conf{k}")
                params[f"__conf{k}"] = f"{ci}%"
    
        # feature filters
        build_feature_conditions(conditions, params)    
        if include_search_filters and translit_search_query:
            conditions.append("LOWER(verbs.translit_verb) COLLATE utf8mb4_bin = LOWER(:tq)")
            params["tq"] = translit_search_query
        if include_search_filters and english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:engq)")
            params["engq"] = english_search_query.lower()
    
        if include_selected_verb and selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin = :selv")
            params["selv"] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selv_gloss")
                params["selv_gloss"] = selected_verb_gloss

    
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        q = f"""
            SELECT DISTINCT verbs.translit_verb
            FROM verbs
            {joins}
            WHERE {where_clause}
        """
        rows = db.session.execute(text(q), params).fetchall()
    
        # fold verbs -> initials in canonical order
        initials = set()
        def extract_initial_letter(word):
            matched = [i for i in initial_letters_list if word.startswith(i)]
            return max(matched, key=len) if matched else ''
        for (tverb,) in rows:
            if tverb:
                init = extract_initial_letter(tverb)
                if init:
                    initials.add(init)
    
        return [i for i in initial_letters_list if i in initials]


    def build_common_components(dependencies, current_level, selected_verb, selected_sources):
        conditions = []
        params = {}
        joins = "FROM verbs v\n"
        select_alias = f'a{current_level}'
    
        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma') or idx == current_level:
                dep_copy = dep.copy()
                dep_copy['idx'] = idx
                active_dependencies.append(dep_copy)
    
        if selected_verb:
            conditions.append("v.translit_verb COLLATE utf8mb4_bin = :selected_verb")
            params['selected_verb'] = selected_verb
            if selected_verb_gloss:
                conditions.append("v.gloss COLLATE utf8mb4_bin = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss
    
        if initial_letter and not selected_verb:
            conditions.append("v.translit_verb COLLATE utf8mb4_bin LIKE :__initial_letter_v")
            params["__initial_letter_v"] = f"{initial_letter}%"
            conflicting = [oi for oi in initial_letters_list
                           if oi != initial_letter and oi.startswith(initial_letter)]
            for n, ci in enumerate(conflicting):
                conditions.append(f"v.translit_verb COLLATE utf8mb4_bin NOT LIKE :__vconf{n}")
                params[f"__vconf{n}"] = f"{ci}%"
    
        # Multi-source filter
        source_filter = build_sources_condition(selected_sources, alias="v.sent_id")
        if source_filter:
            conditions.append(f"({source_filter})")

        if translit_search_query:
            conditions.append(
                "LOWER(v.translit_verb) COLLATE utf8mb4_bin = LOWER(:__tsearch)"
            )
            params["__tsearch"] = translit_search_query
        if english_search_query:
            conditions.append("LOWER(v.gloss) = LOWER(:__esearch)")
            params["__esearch"] = english_search_query.lower()
    
        build_feature_conditions_for_alias(conditions, params)
    
        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += (
                f"JOIN arguments {alias} "
                f"ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"
            )
    
            if idx != current_level:
                if dep.get('deprel'):
                    conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                    params[f'deprel{idx}'] = dep['deprel']
                if dep.get('case_value'):
                    conditions.append(f"{alias}.translit_dep_lemma = :case_value{idx}")
                    params[f'case_value{idx}'] = dep['case_value']
                if dep.get('lemma'):
                    conditions.append(f"{alias}.translit_lemma = :lemma{idx}")
                    params[f'lemma{idx}'] = dep['lemma']
    
        # Distinct arguments if >1 deps active
        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    alias_i = f'a{active_dependencies[i]["idx"]}'
                    alias_j = f'a{active_dependencies[j]["idx"]}'
                    conditions.append(f"{alias_i}.token_id != {alias_j}.token_id")
    
        return joins, conditions, params, select_alias

    # Initialize list to hold options for each dependency set
    dependencies_options = []
    excluded_combinations = [] 

    # Define the new get_dynamic_values function for transliteration
    def get_dynamic_values(
        column,
        dependencies,
        current_level,
        selected_verb=None,
        selected_sources=selected_sources,
        common_components=None,
        excluded_combinations=None
    ):

        # Start from precomputed pieces (from build_common_components)
        if common_components:
            joins, base_conditions, base_params, select_alias = common_components
            conditions = list(base_conditions)
            params = dict(base_params)
        else:
            # Fallback (shouldn’t be hit in our calls)
            select_alias = f'a{current_level}'
            joins = (
                "FROM verbs v\n"
                f"JOIN arguments {select_alias} "
                f"ON v.token_id = {select_alias}.head_id AND v.sent_id = {select_alias}.sent_id\n"
            )
            conditions, params = [], {}
    
        curr = dependencies[current_level] if current_level < len(dependencies) else {}
    
        if curr.get('deprel') and column != 'dep_rel':
            conditions.append(f"{select_alias}.dep_rel = :current_deprel")
            params['current_deprel'] = curr['deprel']
    
        if curr.get('case_value') and column != 'translit_dep_lemma':
            conditions.append(f"{select_alias}.translit_dep_lemma = :current_case_value")
            params['current_case_value'] = curr['case_value']
    
        if curr.get('lemma') and column != 'translit_lemma':
            conditions.append(f"{select_alias}.translit_lemma = :current_lemma")
            params['current_lemma'] = curr['lemma']
    
        where_clause = ' AND '.join(conditions) if conditions else '1=1'
    
        sql = f"""
            SELECT DISTINCT {select_alias}.{column}
            {joins}
            WHERE {where_clause}
            ORDER BY {select_alias}.{column}
        """
    
        # Raw values from DB (list[str], dropping NULLs)
        raw_rows = db.session.execute(text(sql), params).fetchall()
        values = [row[0] for row in raw_rows if row[0] is not None]
    
        # ---------- Exclusion filtering (Python-side), mirroring language behavior ----------
        # Only exclude when selecting this `column` would complete a triple that exactly
        # matches an already-chosen combination in a previous dependency row.
    
        def _get_curr_other_two():
            if column == 'dep_rel':
                return curr.get('case_value'), curr.get('lemma')
            elif column == 'translit_dep_lemma':
                return curr.get('deprel'), curr.get('lemma')
            else:  # 'translit_lemma'
                return curr.get('deprel'), curr.get('case_value')
    
        def _ex_other_two(ex):
            if column == 'dep_rel':
                return ex.get('case_value'), ex.get('lemma')
            elif column == 'translit_dep_lemma':
                return ex.get('deprel'), ex.get('lemma')
            else:
                return ex.get('deprel'), ex.get('case_value')
    
        def _ex_target(ex):
            if column == 'dep_rel':
                return ex.get('deprel')
            elif column == 'translit_dep_lemma':
                return ex.get('case_value')
            else:
                return ex.get('lemma')
    
        other1, other2 = _get_curr_other_two()
        if excluded_combinations and other1 is not None and other2 is not None:
            to_exclude = set()
            for ex in excluded_combinations:
                ex_o1, ex_o2 = _ex_other_two(ex)
                if ex_o1 == other1 and ex_o2 == other2:
                    ex_target_val = _ex_target(ex)
                    if ex_target_val is not None:
                        to_exclude.add(ex_target_val)
            if to_exclude:
                values = [v for v in values if v not in to_exclude]
        # ---------- end exclusion filtering ----------
    
        return values

    # Generate options for each dependency set
    for idx in range(len(dependencies)):
        # Precompute common components
        common_joins, common_conditions, common_params, select_alias = build_common_components(
            dependencies, idx, selected_verb, selected_sources
        )
        common_components = (common_joins, common_conditions, common_params, select_alias)
    
        # Fetch data for each column, passing excluded_combinations
        deprels = get_dynamic_values('dep_rel', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)
        case_values = get_dynamic_values('translit_dep_lemma', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)
        lemmas = get_dynamic_values('translit_lemma', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)
        
        deprels_list = [v for v in deprels if v is not None]
        case_values_list = [v for v in case_values if v is not None]
        lemmas_list = [v for v in lemmas if v is not None]
        
        deprels_sorted = sort_deprels(deprels_list, desired_deprel_order)
        case_values_list.sort()
        lemmas_list.sort()
        
        dependencies_options.append({
            'deprels': deprels_sorted,
            'case_values': case_values_list,
            'lemmas': lemmas_list,
        })
  
        current_dep = dependencies[idx]
        if current_dep.get('deprel') or current_dep.get('case_value') or current_dep.get('lemma'):
            excluded_combinations.append(current_dep)

    has_next_dependency_options = []
    for level in range(len(dependencies)):
        if level + 1 < len(dependencies):
            next_level = level + 1
    
            # Precompute common components for the NEXT level
            common_joins_next, common_conditions_next, common_params_next, select_alias_next = build_common_components(
                dependencies, next_level, selected_verb, selected_sources
            )
            common_components_next = (common_joins_next, common_conditions_next, common_params_next, select_alias_next)
    
            # Fetch options while respecting excluded combinations
            deprels = get_dynamic_values('dep_rel', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)
            case_values = get_dynamic_values('translit_dep_lemma', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)
            lemmas = get_dynamic_values('translit_lemma', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)
            
            has_options = any([deprels, case_values, lemmas])

            has_next_dependency_options.append(has_options)
        else:
            has_next_dependency_options.append(False)

    def get_dynamic_feature_values(dependencies, selected_sources):
        conditions = []   # WHERE fragments
        params = {}       # SQLAlchemy named parameters
        joins = ""        # dynamic JOINs to arguments based on active deps

        # 1) active dependencies → JOIN arguments
        active_deps = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep = dep.copy()     # copy to avoid mutating the original dependencies list
                dep['idx'] = idx     # store index to build stable JOIN aliases a0..a4
                active_deps.append(dep)

        for dep in active_deps:
            alias = f"a{dep['idx']}"
            joins += (
                f"JOIN arguments {alias} "
                f"ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            )
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{dep['idx']}")
                params[f"deprel{dep['idx']}"] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.translit_dep_lemma = :case_value{dep['idx']}")
                params[f"case_value{dep['idx']}"] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.translit_lemma = :lemma{dep['idx']}")
                params[f"lemma{dep['idx']}"] = dep['lemma']

        # 2) ensure distinct arguments if multiple deps
        # If two dependency rows are active, prevent them from being satisfied by the same token_id.
        if len(active_deps) >= 2:
            for i in range(len(active_deps)):
                for j in range(i + 1, len(active_deps)):
                    ai = active_deps[i]['idx']
                    aj = active_deps[j]['idx']
                    conditions.append(f"a{ai}.token_id != a{aj}.token_id")

        # 3) multi-source filter
        # Apply sent_id-based source scoping (German/Dutch/Greek/etc.), if any sources checked.
        src_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if src_filter:
            conditions.append(src_filter)

        # 4) initial filter 
        # Initial-letter restriction should only be applied on the verbs list page.
        # On the sentences page (selected_verb is set), initials are irrelevant.
        if initial_letter and not selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin LIKE :__initial_letter_feats")
            params["__initial_letter_feats"] = f"{initial_letter}%"

            # Conflict-exclusion for multigraph initials:
            # e.g. when initial_letter == 'c', exclude verbs starting with 'cʻ' so 'c' means exactly 'c'.
            conflicting = [oi for oi in initial_letters_list
                           if oi != initial_letter and oi.startswith(initial_letter)]
            for n, ci in enumerate(conflicting):
                conditions.append(
                    f"verbs.translit_verb COLLATE utf8mb4_bin NOT LIKE :__fconf{n}"
                )
                params[f"__fconf{n}"] = f"{ci}%"


        # NOTE: you use exact match here (lowercase equality).
        if translit_search_query:
            conditions.append(
                "LOWER(verbs.translit_verb) COLLATE utf8mb4_bin = LOWER(:feat_tq)"
            )
            params["feat_tq"] = translit_search_query
        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:feat_engq)")
            params["feat_engq"] = english_search_query.lower()

        if selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin = :sv")
            params["sv"] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :svg")
                params["svg"] = selected_verb_gloss

        # 5) feature filters 
        build_feature_conditions(conditions, params)

        # Final WHERE clause (safe fallback if nothing chosen)
        where = " AND ".join(conditions) if conditions else "1=1"

        # CUSTOMIZABLE:
        # - GROUP_CONCAT length may be limited by MySQL group_concat_max_len; if you have many values,
        #   you may need to increase that server-side.
        query = f"""
           SELECT
             GROUP_CONCAT(DISTINCT verbs.VerbForm)     AS all_verbforms,
             GROUP_CONCAT(DISTINCT verbs.Aspect)       AS all_aspects,
             GROUP_CONCAT(DISTINCT verbs.Case)         AS all_cases,
             GROUP_CONCAT(DISTINCT verbs.Connegative)  AS all_negations,
             GROUP_CONCAT(DISTINCT verbs.Mood)         AS all_moods,
             GROUP_CONCAT(DISTINCT verbs.Number)       AS all_numbers,
             GROUP_CONCAT(DISTINCT verbs.Person)       AS all_persons,
             GROUP_CONCAT(DISTINCT verbs.Tense)        AS all_tenses,
             GROUP_CONCAT(DISTINCT verbs.Voice)        AS all_voices
           FROM verbs
           {joins}
           WHERE {where}
           LIMIT 1
        """
        row = db.session.execute(text(query), params).fetchone()

        if not row:
            return {k: set() for k in ["VerbForm", "Aspect", "Case",
                                       "Negation", "Mood", "Number",
                                       "Person", "Tense", "Voice"]}


        def toset(s): return set(s.split(",")) if s else set()

        return {
          "VerbForm":  toset(row.all_verbforms),
          "Aspect":    toset(row.all_aspects),
          "Case":      toset(row.all_cases),
          "Negation":  toset(row.all_negations),
          "Mood":      toset(row.all_moods),
          "Number":    toset(row.all_numbers),
          "Person":    toset(row.all_persons),
          "Tense":     toset(row.all_tenses),
          "Voice":     toset(row.all_voices),
        }


    def build_feature_conditions(conditions, params):
        if selected_verbforms:
            conditions.append("verbs.VerbForm IN :verbforms_list")
            params["verbforms_list"] = tuple(selected_verbforms)
        if selected_aspects:
            conditions.append("verbs.Aspect IN :aspects_list")
            params["aspects_list"] = tuple(selected_aspects)
        if selected_cases:
            conditions.append("verbs.Case IN :cases_list")
            params["cases_list"] = tuple(selected_cases)
        if selected_connegatives:
            conditions.append("verbs.Connegative IN :conneg_list")
            params["conneg_list"] = tuple(selected_connegatives)
        if selected_moods:
            conditions.append("verbs.Mood IN :moods_list")
            params["moods_list"] = tuple(selected_moods)
        if selected_numbers:
            conditions.append("verbs.Number IN :numbers_list")
            params["numbers_list"] = tuple(selected_numbers)
        if selected_persons:
            conditions.append("verbs.Person IN :persons_list")
            params["persons_list"] = tuple(selected_persons)
        if selected_tenses:
            conditions.append("verbs.Tense IN :tenses_list")
            params["tenses_list"] = tuple(selected_tenses)
        if selected_voices:
            conditions.append("verbs.Voice IN :voices_list")
            params["voices_list"] = tuple(selected_voices)


    # Function to get verbs with frequencies 
    def get_verbs_with_frequencies(
        dependencies,
        sort_order,
        order_direction,
        initial_letter,
        translit_search_query,
        english_search_query,
        selected_verb=None,
        selected_verb_gloss=None,
        selected_sources=None
    ):

        conditions = []
        params = {}
        joins = ''

        # Build active_dependencies
        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx  # Add idx to dep (side-effect)
                active_dependencies.append(dep)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.translit_dep_lemma = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.translit_lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        build_feature_conditions(conditions, params)

        # Initial filter applies only on list page (no selected verb)
        # Includes multigraph-conflict exclusions.
        if initial_letter and not selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin LIKE :initial_letter")
            params['initial_letter'] = f"{initial_letter}%"
            conflicting_initials = [oi for oi in initial_letters_list if oi != initial_letter and oi.startswith(initial_letter)]
            for idx, ci in enumerate(conflicting_initials):
                conditions.append(f"verbs.translit_verb COLLATE utf8mb4_bin NOT LIKE :conflict_initial{idx}")
                params[f'conflict_initial{idx}'] = f"{ci}%"

        # Source filter (sent_id-based)
        source_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if source_filter:
            conditions.append(source_filter)

        # Transliterated search query (exact match, case-insensitive)
        if translit_search_query:
            conditions.append("LOWER(verbs.translit_verb) COLLATE utf8mb4_bin = LOWER(:translit_search_query)")
            params['translit_search_query'] = translit_search_query

        # English search query (exact match, case-insensitive)
        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:english_search_query)")
            params['english_search_query'] = english_search_query.lower()

        # Include selected verb condition
        if selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin = :selected_verb")
            params['selected_verb'] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss

        # Final WHERE
        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        # Query: group by (translit_verb, gloss) and count distinct verb occurrences.
        query = f"""
        SELECT verbs.translit_verb, verbs.gloss, COUNT(DISTINCT verbs.token_id, verbs.sent_id) as frequency
        FROM verbs
        {joins}
        WHERE {where_clause}
        GROUP BY verbs.translit_verb, verbs.gloss
        """

        # Sorting:
        # - by frequency if requested
        # - otherwise by translit_verb alphabetically
        if sort_order == 'frequency':
            query += f" ORDER BY frequency {'ASC' if order_direction == 'asc' else 'DESC'}"
        else:
            query += f" ORDER BY verbs.translit_verb {'ASC' if order_direction == 'asc' else 'DESC'}"

        return db.session.execute(text(query), params).fetchall()


    # Get verbs with frequencies
    verbs_result = get_verbs_with_frequencies(
        dependencies, sort_order, order_direction, initial_letter, translit_search_query, english_search_query,
        selected_verb, selected_verb_gloss, selected_sources=selected_sources
    )

    verbs_with_frequencies = [
        {'translit_verb': row[0], 'gloss': row[1], 'frequency': row[2]}
        for row in verbs_result
    ]

    total_verb_count = len(verbs_with_frequencies)
    total_occurrence_count = sum(int(v['frequency']) for v in verbs_with_frequencies)

    # Get total sentence count 
    def get_total_sentence_count(
        dependencies,
        initial_letter,
        translit_search_query,
        english_search_query,
        selected_verb=None,
        selected_verb_gloss=None,
        selected_sources=None
    ):

        if selected_sources is None:
            selected_sources = []

        conditions = []
        params = {}
        joins = ''

        # Build active_dependencies
        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx  # Add idx to dep (side-effect)
                active_dependencies.append(dep)

        # Join arguments for active deps and constrain them.
        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.translit_dep_lemma = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.translit_lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        # Distinctness across multiple deps
        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        # Initial letter restriction (list page only)
        if initial_letter and not selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin LIKE :initial_letter")
            params['initial_letter'] = f"{initial_letter}%"
            conflicting_initials = [oi for oi in initial_letters_list if oi != initial_letter and oi.startswith(initial_letter)]
            for idx, ci in enumerate(conflicting_initials):
                conditions.append(f"verbs.translit_verb COLLATE utf8mb4_bin NOT LIKE :conflict_initial{idx}")
                params[f'conflict_initial{idx}'] = f"{ci}%"

        # Exact-match verb search
        if translit_search_query:
            conditions.append("LOWER(verbs.translit_verb) COLLATE utf8mb4_bin = LOWER(:translit_search_query)")
            params['translit_search_query'] = translit_search_query

        # Exact-match gloss search
        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:english_search_query)")
            params['english_search_query'] = english_search_query.lower()

        # Source filter (note the comment about placement; functionally it can be anywhere before WHERE assembly)
        source_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if source_filter:
            conditions.append(source_filter)

        # Selected verb restriction (sentences page semantics)
        if selected_verb:
            conditions.append("verbs.translit_verb COLLATE utf8mb4_bin = :selected_verb")
            params['selected_verb'] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss

        # Feature filters
        build_feature_conditions(conditions, params)

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        # Count distinct sentences via verbs table hits.
        query = f"""
        SELECT COUNT(DISTINCT verbs.sent_id) as total_sentences
        FROM verbs
        {joins}
        WHERE {where_clause}
        """

        result = db.session.execute(text(query), params).fetchone()
        return result.total_sentences if result else 0


    # Get total sentence count
    total_sentence_count = get_total_sentence_count(
        dependencies, initial_letter, translit_search_query, english_search_query, selected_verb, selected_verb_gloss,
        selected_sources=selected_sources
    )

    def format_tooltip(word):
        gloss_part = word['gloss'].replace(" ", "\u00A0") if word['gloss'] else ""
        feat_part = word['feat'] if word['feat'] else ""
        return f"{gloss_part}.{feat_part}" if gloss_part and feat_part else gloss_part or feat_part

    PAGE_TOKEN_SIZE = 50  # fixed window size for token-based pagination

    def _build_sentence_where_for_selected_verb_translit(selected_verb, selected_verb_gloss, dependencies, selected_sources):
        params = {}
        conditions = []
        joins = "JOIN verbs v ON s.sent_id = v.sent_id\n"

        # If not present, force empty result set using 0=1.
        if not selected_verb:
            return joins, ["0=1"], params

        conditions.append("v.translit_verb COLLATE utf8mb4_bin = :sel_v")
        params["sel_v"] = selected_verb
        if selected_verb_gloss:
            conditions.append("v.gloss = :sel_vg")
            params["sel_vg"] = selected_verb_gloss

        src_filter = build_sources_condition(selected_sources, alias="s.sent_id")
        if src_filter:
            conditions.append(src_filter)

        active = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma'):
                active.append((idx, dep))

        # LEFT JOIN each active dependency row so we can constrain it in WHERE.
        for idx, dep in active:
            a = f"a{idx}"
            joins += f"LEFT JOIN arguments {a} ON v.token_id = {a}.head_id AND v.sent_id = {a}.sent_id\n"
            if dep.get('deprel'):
                conditions.append(f"{a}.dep_rel = :deprel{idx}")
                params[f"deprel{idx}"] = dep['deprel']
            if dep.get('case_value'):
                conditions.append(f"{a}.translit_dep_lemma = :case_value{idx}")
                params[f"case_value{idx}"] = dep['case_value']
            if dep.get('lemma'):
                conditions.append(f"{a}.translit_lemma = :lemma{idx}")
                params[f"lemma{idx}"] = dep['lemma']

        # Distinct arguments if multiple deps are active
        if len(active) >= 2:
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    iidx, jidx = active[i][0], active[j][0]
                    conditions.append(f"a{iidx}.token_id != a{jidx}.token_id")

        # Feature filters on v.*
        if selected_verbforms:    conditions.append("v.VerbForm IN :vf");        params["vf"]   = tuple(selected_verbforms)
        if selected_aspects:      conditions.append("v.Aspect   IN :asp");       params["asp"]  = tuple(selected_aspects)
        if selected_cases:        conditions.append("v.Case     IN :cas");       params["cas"]  = tuple(selected_cases)
        if selected_connegatives: conditions.append("v.Connegative IN :neg");    params["neg"]  = tuple(selected_connegatives)
        if selected_moods:        conditions.append("v.Mood     IN :mood");      params["mood"] = tuple(selected_moods)
        if selected_numbers:      conditions.append("v.Number   IN :num");       params["num"]  = tuple(selected_numbers)
        if selected_persons:      conditions.append("v.Person   IN :per");       params["per"]  = tuple(selected_persons)
        if selected_tenses:       conditions.append("v.Tense    IN :ten");       params["ten"]  = tuple(selected_tenses)
        if selected_voices:       conditions.append("v.Voice    IN :voi");       params["voi"]  = tuple(selected_voices)

        return joins, conditions, params


    def get_selected_verb_totals_and_page_ids_translit(
        selected_verb,
        selected_verb_gloss,
        dependencies,
        selected_sources,
        *,
        page,
        per_page,
        offset
    ):

        joins, conds, params = _build_sentence_where_for_selected_verb_translit(
            selected_verb, selected_verb_gloss, dependencies, selected_sources
        )
        where_clause = " AND ".join(conds) if conds else "1=1"

        # Build a CTE "filtered" with token_hits per sentence.
        q_totals = f"""
        WITH filtered AS (
          SELECT s.sent_id AS sent_id,
                 COUNT(DISTINCT v.token_id) AS token_hits
          FROM sentences s
          {joins}
          WHERE {where_clause}
          GROUP BY s.sent_id
        )
        SELECT
          (SELECT COUNT(*) FROM filtered)                  AS total_sentences,
          (SELECT COALESCE(SUM(token_hits),0) FROM filtered) AS total_tokens
        ;
        """
        row = db.session.execute(text(q_totals), params).fetchone()
        total_sentences = int(row.total_sentences or 0)
        total_tokens    = int(row.total_tokens or 0)

        token_offset = max(0, (page - 1) * PAGE_TOKEN_SIZE)
        window_end   = token_offset + PAGE_TOKEN_SIZE

        if token_offset >= total_tokens:
            return total_sentences, total_tokens, token_offset, [], 0

        q_rows = f"""
        WITH filtered AS (
          SELECT s.sent_id AS sent_id,
                 COUNT(DISTINCT v.token_id) AS token_hits
          FROM sentences s
          {joins}
          WHERE {where_clause}
          GROUP BY s.sent_id
        )
        SELECT sent_id, token_hits
        FROM filtered
        ORDER BY sent_id
        """
        all_rows = db.session.execute(text(q_rows), params).fetchall()

        cum = 0
        page_sent_ids = []
        page_token_total = 0

        i = 0
        while i < len(all_rows) and cum + int(all_rows[i].token_hits or 0) <= token_offset:
            cum += int(all_rows[i].token_hits or 0)
            i += 1

        while i < len(all_rows) and cum < window_end:
            hits = int(all_rows[i].token_hits or 0)
            if hits > 0:
                page_sent_ids.append(all_rows[i].sent_id)
                room = window_end - cum
                add  = min(hits, room)  
                page_token_total += add
                cum += hits             
            i += 1

        prev_tokens_cum = token_offset
        return total_sentences, total_tokens, prev_tokens_cum, page_sent_ids, page_token_total


    def get_sentences_scoped_translit(selected_verb, selected_verb_gloss, dependencies, page_sent_ids, selected_sources=None):
        if not selected_verb:
            return []
        if selected_sources is None:
            selected_sources = []

        conditions = ["v.translit_verb COLLATE utf8mb4_bin = :selected_verb"]
        params = {'selected_verb': selected_verb}
        joins = ''

        conditions.append("s.sent_id IN :page_ids")
        params["page_ids"] = tuple(page_sent_ids if page_sent_ids else [-1])  # guard empty IN

        src_filter = build_sources_condition(selected_sources, alias="s.sent_id")
        if src_filter:
            conditions.append(src_filter)

        if selected_verb_gloss:
            conditions.append("v.gloss = :selected_verb_gloss")
            params['selected_verb_gloss'] = selected_verb_gloss

        active = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma'):
                dep = {**dep, 'idx': idx}
                active.append(dep)
                a = f'a{idx}'
                joins += f"LEFT JOIN arguments {a} ON v.token_id = {a}.head_id AND v.sent_id = {a}.sent_id\n"
                if dep.get('deprel'):
                    conditions.append(f"{a}.dep_rel = :deprel{idx}")
                    params[f'deprel{idx}'] = dep['deprel']
                if dep.get('case_value'):
                    conditions.append(f"{a}.translit_dep_lemma = :case_value{idx}")
                    params[f'case_value{idx}'] = dep['case_value']
                if dep.get('lemma'):
                    conditions.append(f"{a}.translit_lemma = :lemma{idx}")
                    params[f'lemma{idx}'] = dep['lemma']

        if len(active) >= 2:
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    iidx = active[i]['idx']; jidx = active[j]['idx']
                    conditions.append(f"a{iidx}.token_id != a{jidx}.token_id")

        # Feature filters (v.*)
        # NOTE: Here you bind the longer *_list param names, consistent with build_feature_conditions().
        if selected_verbforms:    conditions.append("v.VerbForm IN :verbforms_list"); params["verbforms_list"] = tuple(selected_verbforms)
        if selected_aspects:      conditions.append("v.Aspect IN :aspects_list");     params["aspects_list"]   = tuple(selected_aspects)
        if selected_cases:        conditions.append("v.Case IN :cases_list");         params["cases_list"]     = tuple(selected_cases)
        if selected_connegatives: conditions.append("v.Connegative IN :conneg_list"); params["conneg_list"]    = tuple(selected_connegatives)
        if selected_moods:        conditions.append("v.Mood IN :moods_list");         params["moods_list"]     = tuple(selected_moods)
        if selected_numbers:      conditions.append("v.Number IN :numbers_list");     params["numbers_list"]   = tuple(selected_numbers)
        if selected_persons:      conditions.append("v.Person IN :persons_list");     params["persons_list"]   = tuple(selected_persons)
        if selected_tenses:       conditions.append("v.Tense IN :tenses_list");       params["tenses_list"]    = tuple(selected_tenses)
        if selected_voices:       conditions.append("v.Voice IN :voices_list");       params["voices_list"]    = tuple(selected_voices)

        where_clause = ' AND '.join(conditions)

        sentences_basic_info = db.session.execute(text(f"""
            SELECT DISTINCT s.sent_id, s.transliterated_text AS text, s.translated_text
            FROM sentences s
            JOIN verbs v ON s.sent_id = v.sent_id
            {joins}
            WHERE {where_clause}
            GROUP BY s.sent_id, s.transliterated_text, s.translated_text
            ORDER BY s.sent_id
        """), params).fetchall()
        if not sentences_basic_info:
            return []

        verb_token_ids = db.session.execute(text(f"""
            SELECT DISTINCT v.token_id, v.sent_id
            FROM verbs v
            JOIN sentences s ON s.sent_id = v.sent_id
            {joins}
            WHERE {where_clause}
        """), params).fetchall()

        from collections import defaultdict
        verb_token_ids_per_sent = defaultdict(list)
        for token_id, sent_id in verb_token_ids:
            verb_token_ids_per_sent[sent_id].append(int(token_id))

        # Fetch all words for these sentence ids from words table (not using joins).
        sent_ids = [row[0] for row in sentences_basic_info]
        safe_sent_ids = tuple(sent_ids) if sent_ids else tuple([-1])

        words_all = db.session.execute(text("""
            SELECT w.sent_id, w.token_id, w.translit AS form, CAST(w.feat AS CHAR), w.gloss, w.head_id, w.dep_rel, w.pos
            FROM words w
            WHERE w.sent_id IN :sent_ids
            ORDER BY w.sent_id, w.token_id
        """), {"sent_ids": safe_sent_ids}).fetchall()

        words_by_sent = defaultdict(list)
        for row in words_all:
            words_by_sent[row.sent_id].append(row)

        args_all = db.session.execute(text("""
            SELECT a.sent_id, a.head_id, a.token_id, a.dep_rel, a.cdep_token_id, a.second_cdep_token_id, a.fdep_token_id
            FROM arguments a
            WHERE a.sent_id IN :sent_ids
        """), {"sent_ids": safe_sent_ids}).fetchall()

        args_by_sent = defaultdict(list)
        for a in args_all:
            args_by_sent[a.sent_id].append(a)

        # For each sentence, build word objects, mark selected verb tokens, mark arguments,
        # create relations, then do display-merge token logic.
        sentences = []
        for sent_id, text_val, translated_text in sentences_basic_info:
            words = words_by_sent.get(sent_id, [])
            if not words:
                continue

            word_map = {}
            for w in words:
                feat = w[3] if w[3] != 'None' else None
                if feat:
                    feat = '|'.join(set(feat.split('|'))) 
                token_id_int = int(w[1])
                head_id_int  = int(w[5]) if w[5] is not None else None
                word_map[token_id_int] = {
                    'token_id': token_id_int,
                    'form': w[2] or '',
                    'feat': feat,
                    'gloss': w[4] if w[4] != 'None' else None,
                    'head_id': head_id_int,
                    'dep_rel': w[6],
                    'pos': w[7],
                    'is_selected_verb': False,
                    'is_argument': False,
                    'is_case_dependent': False,
                    'is_fixed_dependent': False,
                    'tokens_info': [{'gloss': w[4] if w[4] != 'None' else None, 'feat': feat}]
                }

            # Token IDs of the selected verb occurrences in this sentence.
            selected_token_ids = verb_token_ids_per_sent.get(sent_id, [])
            if not selected_token_ids:
                continue

            relations = []
            heads = set(selected_token_ids)

            # Only keep arguments whose head is one of the selected verb tokens.
            relevant_args = [a for a in args_by_sent.get(sent_id, []) if int(a.head_id) in heads]

            for vid in selected_token_ids:
                if vid in word_map:
                    word_map[vid]['is_selected_verb'] = True

            # Convert argument rows into relation edges and mark token roles.
            for arg in relevant_args:
                head_id = int(arg.head_id)
                arg_id  = int(arg.token_id) if arg.token_id is not None else None
                cdep_id = int(arg.cdep_token_id) if arg.cdep_token_id is not None else None
                sdep_id = int(arg.second_cdep_token_id) if arg.second_cdep_token_id is not None else None
                fdep_id = int(arg.fdep_token_id) if arg.fdep_token_id is not None else None
                dep_rel = arg.dep_rel or 'argument'

                if arg_id and arg_id in word_map:
                    word_map[arg_id]['is_argument'] = True
                    relations.append({'from': head_id, 'to': arg_id, 'dep_rel': dep_rel})

                # Case dependents (cdep/second) are attached after arg or after head if arg missing.
                if cdep_id and cdep_id in word_map:
                    word_map[cdep_id]['is_case_dependent'] = True
                    relations.append({'from': arg_id if arg_id else head_id, 'to': cdep_id, 'dep_rel': 'case_dependency'})

                if sdep_id and sdep_id in word_map:
                    word_map[sdep_id]['is_case_dependent'] = True
                    relations.append({'from': arg_id if arg_id else head_id, 'to': sdep_id, 'dep_rel': 'case_dependency'})

                # Fixed dependents attach to deepest available dependent.
                if fdep_id and fdep_id in word_map:
                    word_map[fdep_id]['is_fixed_dependent'] = True
                    if sdep_id:
                        relations.append({'from': sdep_id, 'to': fdep_id, 'dep_rel': 'fixed_dependency'})
                    elif cdep_id:
                        relations.append({'from': cdep_id, 'to': fdep_id, 'dep_rel': 'fixed_dependency'})
                    elif arg_id:
                        relations.append({'from': arg_id, 'to': fdep_id, 'dep_rel': 'fixed_dependency'})
                    else:
                        relations.append({'from': head_id, 'to': fdep_id, 'dep_rel': 'fixed_dependency'})

            # merge tokens (same language logic)
            original_words = list(word_map.values())
            merged_words = []

            # These sets define the orthographic/token-attachment rules for transliteration display.
            tokens_attach_to_next = {'y', 'z', 'cʻ', 'čʻ', 'Y', 'Z', 'Cʻ', 'Čʻ'}
            tokens_attach_to_prev = {'s', 'd', 'n', ';', '.', ',', ':'}
            special_attach_tokens = {'?', '!'}
            vowels = {'a', 'e', 'ē', 'ǝ', 'i', 'o', 'u'}

            i = 0
            while i < len(original_words):
                current_token = original_words[i]
                current_form = current_token['form']
                current_token_ids = [str(current_token['token_id'])]
                current_attrs = current_token.copy()
                current_attrs['token_id'] = '_'.join(current_token_ids)
                tokens_info = current_token.get('tokens_info', [{'gloss': current_token.get('gloss'), 'feat': current_token.get('feat')}])
                i += 1

                # Special attach: '?' and '!' are inserted after the last vowel in the previous merged word.
                if current_form in special_attach_tokens and merged_words:
                    last_word = merged_words.pop()
                    last_form = last_word['form']
                    vowel_indices = [idx for idx, char in enumerate(last_form) if char in vowels]
                    insert_pos = (vowel_indices[-1] + 1) if vowel_indices else len(last_form)
                    last_word['form'] = last_form[:insert_pos] + current_form + last_form[insert_pos:]
                    last_word['tokens_info'] += tokens_info
                    for k in ('is_selected_verb','is_argument','is_case_dependent','is_fixed_dependent'):
                        last_word[k] = last_word[k] or current_attrs[k]
                    last_word['token_id'] = f"{last_word['token_id']}_{current_attrs['token_id']}"
                    tooltip_parts = []
                    for ti in last_word['tokens_info']:
                        g, f = ti.get('gloss'), ti.get('feat')
                        tooltip_parts.append(f"{g}.{f}" if g and f else (g or f or ''))
                    last_word['gloss'] = '='.join([p for p in tooltip_parts if p])
                    merged_words.append(last_word)
                    continue

                # Attach punctuation-like tokens to previous word by concatenation.
                while current_form in tokens_attach_to_prev and merged_words:
                    last_word = merged_words.pop()
                    current_form = last_word['form'] + current_form
                    current_token_ids = last_word['token_id'].split('_') + current_token_ids
                    tokens_info = last_word['tokens_info'] + tokens_info
                    for k in ('is_selected_verb','is_argument','is_case_dependent','is_fixed_dependent'):
                        current_attrs[k] = current_attrs[k] or last_word[k]
                    current_attrs['pos'] = current_attrs['pos'] or last_word['pos']

                # Attach clitic-like tokens to the next token (concatenate forward).
                while current_form in tokens_attach_to_next and i < len(original_words):
                    nxt = original_words[i]
                    current_form += nxt['form']
                    current_token_ids.append(str(nxt['token_id']))
                    tokens_info += nxt.get('tokens_info', [{'gloss': nxt.get('gloss'), 'feat': nxt.get('feat')}])
                    for k in ('is_selected_verb','is_argument','is_case_dependent','is_fixed_dependent'):
                        current_attrs[k] = current_attrs[k] or nxt[k]
                    current_attrs['pos'] = current_attrs['pos'] or nxt['pos']
                    i += 1
                    if nxt['form'] not in tokens_attach_to_next:
                        break

                # Build combined tooltip string for merged token.
                tooltip_parts = []
                for ti in tokens_info:
                    g, f = ti.get('gloss'), ti.get('feat')
                    tooltip_parts.append(f"{g}.{f}" if g and f else (g or f or ''))
                combined_tooltip = '='.join([p for p in tooltip_parts if p])

                # Write merged results back into attrs.
                current_attrs['form'] = current_form
                current_attrs['token_id'] = '_'.join(current_token_ids)
                current_attrs['gloss'] = combined_tooltip
                current_attrs['tokens_info'] = tokens_info
                merged_words.append(current_attrs)

            # Final sentence object for template rendering.
            sentences.append({
                'sent_id': sent_id,
                'text': text_val,
                'translated_text': translated_text,
                'words': merged_words,
                'original_words': list(word_map.values()),
                'relations': relations
            })

        return sentences


    def get_sentences_translit(selected_verb, selected_verb_gloss, dependencies, selected_sources=None, page_sent_ids=None):
        # Wrapper to keep a single call-site; we always pass page_sent_ids from the pager.
        return get_sentences_scoped_translit(selected_verb, selected_verb_gloss, dependencies, page_sent_ids or [], selected_sources)


    # Initialize pagination + sentence page state.
    page_occurrence_start = 0
    page_occurrence_end   = 0
    has_prev = False
    has_next = False
    sentences = []

    # These variables are used in the template; initialize them even in list-mode.
    selected_verb_sentence_count = 0
    selected_verb_token_count = 0
    prev_tokens_cum = 0
    page_sent_ids = []
    page_token_total = 0

    # Sentences page mode (only when selected_verb exists)
    if selected_verb:
        (selected_verb_sentence_count,
         selected_verb_token_count,
         prev_tokens_cum,
         page_sent_ids,
         page_token_total) = get_selected_verb_totals_and_page_ids_translit(
            selected_verb, selected_verb_gloss, dependencies, selected_sources,
            page=page, per_page=PAGE_TOKEN_SIZE, offset=(page-1)*PAGE_TOKEN_SIZE
        )

        if page_token_total > 0:
            page_occurrence_start = prev_tokens_cum + 1
            page_occurrence_end   = prev_tokens_cum + page_token_total

        has_prev = page > 1
        has_next = (prev_tokens_cum + page_token_total) < selected_verb_token_count

        sentences = get_sentences_translit(
            selected_verb, selected_verb_gloss, dependencies,
            selected_sources=selected_sources,
            page_sent_ids=page_sent_ids
        )

        total_sentence_count = selected_verb_sentence_count
    else:
        sentences = []
        # total_sentence_count was already computed for the list page above


    # Build the initial-letter bar so it only includes initials that exist under current filters.
    initials_filtered = get_initials_under_filters_translit(
        dependencies=dependencies,
        selected_sources=selected_sources,

        initial_letter=None if selected_verb else initial_letter,

        include_selected_verb=False,

        include_search_filters=False if selected_verb else True
    )

    base_args = MultiDict(request.args)


    if selected_verb:
        for k in ('selected_verb', 'selected_verb_gloss', 'translit_search_query', 'english_search_query'):
            try:
                base_args.pop(k)
            except KeyError:
                pass

    list_args = MultiDict(base_args)
    for k in ('selected_verb', 'selected_verb_gloss', 'page'):
        try:
            list_args.pop(k)
        except KeyError:
            pass
    list_args.setlist('page', ['1'])  # reset paging on list
    verbs_list_qs = urlencode(list(list_args.lists()), doseq=True)

    effective_sources = selected_sources

    initial_links = []
    for letter in initials_filtered:
        args_copy = MultiDict(base_args)
        args_copy.setlist('initial', [letter])

        if effective_sources:
            args_copy.setlist('source_checkbox_submitted', ['1'])
            args_copy.setlist('selected_source', effective_sources)

        url = url_for('translit') + '?' + urlencode(list(args_copy.lists()), doseq=True)
        initial_links.append({'letter': letter, 'url': url})

    clear_args = MultiDict(base_args)
    clear_args.setlist('initial', [''])
    if effective_sources:
        clear_args.setlist('source_checkbox_submitted', ['1'])
        clear_args.setlist('selected_source', effective_sources)
    clear_initials_url = url_for('translit') + '?' + urlencode(list(clear_args.lists()), doseq=True)

    initial_letters_in_use = initials_filtered


    def generate_brat_data(sentences):
        """
        Build brat-style visualisation payload for each sentence:
        - "text" (reconstructed from unmerged tokens)
        - "entities" with character offsets
        - optional "attributes" (Case=...)
        - "relations" derived from sentence['relations']

        IMPORTANT:
        - Uses sentence['original_words'] (unmerged tokens), not the merged display tokens.
        """
        if not sentences:
            return None

        for sentence in sentences:
            words = sentence['original_words']  # Use unmerged tokens for brat visualization
            relations = sentence.get('relations', [])

            text = ''
            offsets = []
            for word in words:
                word_form = word['form'] or ''
                start_offset = len(text)
                text += word_form + ' '
                end_offset = len(text) - 1  # Exclude the trailing space
                offsets.append((start_offset, end_offset))

            text = text.strip()

            entities = []
            attributes = []
            brat_relations = []

            # Map from token_id to entity ID (T1, T2, ...)
            token_id_to_entity_id = {}
            for idx, (word, (start, end)) in enumerate(zip(words, offsets)):
                entity_id = f"T{idx+1}"
                token_id_to_entity_id[int(word['token_id'])] = entity_id


                if word.get('is_selected_verb'):
                    entity_type = f"SelectedVerb_{word.get('pos', 'Token')}"
                else:
                    entity_type = word.get('pos', 'Token')

                entities.append([entity_id, entity_type, [[start, end]]])

                if word.get('feat'):
                    feat_parts = word['feat'].split('|')
                    for part in feat_parts:
                        if part.startswith('Case='):
                            case_value = part.split('=')[1]
                            attr_id = f"A{idx+1}"
                            attributes.append([attr_id, 'Case', entity_id, case_value])
                            break  # Stop after finding 'Case'

            # Add relations using the 'relations' from the sentence
            for idx, rel in enumerate(relations):
                from_token_id = int(rel['from'])
                to_token_id = int(rel['to'])
                dep_rel = rel.get('dep_rel') or 'relation'
                from_entity = token_id_to_entity_id.get(from_token_id)
                to_entity = token_id_to_entity_id.get(to_token_id)
                if from_entity and to_entity:
                    relation_id = f"R{idx+1}"
                    brat_relations.append([relation_id, dep_rel, [['Governor', from_entity], ['Dependent', to_entity]]])

            brat_data = {
                'text': text,
                'entities': entities,
                'attributes': attributes,
                'relations': brat_relations
            }

            # Attach brat_data to the sentence
            sentence['brat_data'] = brat_data

    # Call generate_brat_data after getting sentences
    generate_brat_data(sentences)

    # Compute feature value “universe” under current filters, then sort for template.
    raw_feature_values = get_dynamic_feature_values(dependencies, selected_sources)
    server_feature_values = {feat: sorted(list(vals)) for feat, vals in raw_feature_values.items()}

    # 1) Build a simple dictionary of selected features => their chosen values
    user_feature_selections = {}

    if selected_verbforms:
        user_feature_selections['VerbForm'] = selected_verbforms

    if selected_aspects:
        user_feature_selections['Aspect'] = selected_aspects

    if selected_cases:
        user_feature_selections['Case'] = selected_cases

    if selected_connegatives:
        user_feature_selections['Negation'] = selected_connegatives

    if selected_moods:
        user_feature_selections['Mood'] = selected_moods

    if selected_numbers:
        user_feature_selections['Number'] = selected_numbers

    if selected_persons:
        user_feature_selections['Person'] = selected_persons

    if selected_tenses:
        user_feature_selections['Tense'] = selected_tenses

    if selected_voices:
        user_feature_selections['Voice'] = selected_voices

    def translit_to_language_text(s: str) -> str:
        """
        Convert translit string into given language-script string using initial_map_translit_to_language.
        Implementation:
        - Replace longer keys first to handle multigraphs safely ('tʻ', 'čʻ', 'aw', ...).
        - Then single-letter keys.
        """
        keys = sorted(initial_map_translit_to_language.keys(), key=len, reverse=True)
        out = s
        for k in keys:
            out = out.replace(k, initial_map_translit_to_language[k])
        return out

    def normalize_case_value_for_language(val: str) -> str:
        """
        Convert the translit case_value into the given language version.
        Special-case: if value is "X+Y", keep the left side as-is and convert the right side.
        """
        if '+' in val:
            left, right = val.split('+', 1)
            return f"{left.strip()} + {translit_to_language_text(right.strip())}"
        return translit_to_language_text(val)

    # ───────────────────────────────────────────────────────────────────────────
    # TRANSLIT → language: build language-switch URL
    # ───────────────────────────────────────────────────────────────────────────
    base_query = request.query_string.decode('utf-8')
    qs_t = parse_qs(base_query, keep_blank_values=True)

    if qs_t.get('selected_source'):
        qs_t['source_checkbox_submitted'] = ['1']

    # Transliteration page uses translit_search_query/translit_lemma; home() uses language_search_query/case_dependant_lemma.
    if 'translit_search_query' in qs_t:
        qs_t['language_search_query'] = qs_t.pop('translit_search_query')
    if 'translit_lemma' in qs_t:
        qs_t['case_dependant_lemma'] = qs_t.pop('translit_lemma')

    # Initial (translit multigraph → language single char), session-aware
    effective_initial_tr = initial_letter or ''
    if effective_initial_tr:
        mapped = initial_map_translit_to_language.get(effective_initial_tr, '')
        if mapped:
            qs_t['initial'] = [mapped]
        else:
            qs_t.pop('initial', None)
    else:
        qs_t.pop('initial', None)
        qs_t.pop('reset', None)

    lemma_keys_t = ['case_dependant_lemma'] + [f'co_occurring_lemma_{i}' for i in range(2, 6)]
    enc_keys_t   = ['case_value'] + [f'co_occurring_case_value_{i}' for i in range(2, 6)]

    tlemmas = [qs_t[k][0] for k in lemma_keys_t if k in qs_t and qs_t[k] and qs_t[k][0]]
    arm_map = _fetch_arm_for_arg_tlemmas(tlemmas)  # {translit_lemma -> lemma}
    for k in lemma_keys_t:
        if k in qs_t and qs_t[k] and qs_t[k][0]:
            tval = qs_t[k][0]
            aval = arm_map.get(tval)
            if aval:
                qs_t[k] = [aval]
            else:
                qs_t.pop(k, None)  # avoid over-filtering with unmapped value

    present_enc_keys_t = []
    tbits = []                  # e.g. ["zhet", "arj" ...] translit dep-lemmas
    key_to_tbit = {}
    for k in enc_keys_t:
        val = qs_t.get(k, [''])[0]
        if val:
            present_enc_keys_t.append(k)
            key_to_tbit[k] = val
            tbits.append(val)

    tbit_to_armbit = _fetch_arm_for_dep_tbits(list(set(tbits)))  # {tbit -> dep_bit_arm}

    for k in enc_keys_t:
        qs_t.pop(k, None)

    def _ctx_for_key(kname: str):
        """
        Return (dep_rel, tlemma) for the same dependency row as kname.

        - For main row: dep_rel is qs_t['syntactic_relation'] (already in qs_t),
          tlemma is read from ORIGINAL base_query's translit_lemma (because qs_t got renamed).
        - For co-occurring rows: same pattern for that row index.
        """
        if kname == 'case_value':
            dep_rel = qs_t.get('syntactic_relation', [''])[0] or None
            tlemma = parse_qs(base_query).get('translit_lemma', [''])[0] or None
            return dep_rel, tlemma

        m = re.match(r'co_occurring_case_value_(\d+)$', kname)
        if m:
            idx = int(m.group(1))
            dep_rel = qs_t.get(f'co_occurring_deprel_{idx}', [''])[0] or None
            tlemma = parse_qs(base_query).get(f'co_occurring_lemma_{idx}', [''])[0] or None
            return dep_rel, tlemma

        return None, None

    for k in present_enc_keys_t:
        tbit = key_to_tbit.get(k)
        armbit = tbit_to_armbit.get(tbit) if tbit else None
        if not armbit:
            continue

        dep_rel_ctx, tlemma_ctx = _ctx_for_key(k)
        cv_map = _fetch_case_values_for_tbits([tbit], dep_rel=dep_rel_ctx, tlemma=tlemma_ctx)
        # cv_map[tbit] => set of Armenian case_value strings
        cand = sorted(list(cv_map.get(tbit, set())))
        if len(cand) == 1:
            qs_t[k] = [cand[0]]  # unique → safe to inject
        else:
            pass

    # Selected verb: translit_verb → language lemma
    if 'selected_verb' in qs_t and qs_t['selected_verb']:
        tverb = qs_t['selected_verb'][0]
        row = db.session.execute(
            text("SELECT lemma FROM verbs WHERE translit_verb = :tv COLLATE utf8mb4_bin LIMIT 1"),
            {'tv': tverb}
        ).fetchone()
        if row and row.lemma:
            qs_t['selected_verb'] = [row.lemma]
        else:
            qs_t.pop('selected_verb', None)
            qs_t.pop('selected_verb_gloss', None)

    qs_t['page'] = ['1']
    switch_qs_t  = urlencode(qs_t, doseq=True)
    switch_url   = url_for('home') + ('?' + switch_qs_t if switch_qs_t else '')
    # ───────────────────────────────────────────────────────────────────────────

    context = {
        'verbs_with_frequencies': verbs_with_frequencies,
        'sort_order': sort_order,
        'order_direction': order_direction,
        'initial_letters_in_use': initial_letters_in_use,
        'initial_letters': initials_filtered,
        'initial_links': initial_links,
        'clear_initials_url': clear_initials_url,
        'initial_letter': initial_letter,
        'dependencies': dependencies,
        'sentences': sentences,
        'selected_verb': selected_verb,
        'selected_verb_url': selected_verb_url,
        'selected_verb_gloss': selected_verb_gloss,
        'dependency_visible_flags': dependency_visible_flags,
        'total_sentence_count': total_sentence_count,
        'translit_search_query': translit_search_query,
        'english_search_query': english_search_query,
        'is_translit_page': is_translit_page,
        'last_visible_dependency_index': last_visible_dependency_index,
        'dependency_has_selection': dependency_has_selection,
        'has_next_dependency_options': has_next_dependency_options,
        'dependencies_options': dependencies_options,
        'selected_sources': selected_sources,
        'verb_features_config': verb_features_config,
        'server_feature_values': server_feature_values,
        'user_feature_selections': user_feature_selections,
        'base_query': base_query,
        'total_verb_count': total_verb_count,
        'total_occurrence_count': total_occurrence_count,
        'selected_verb_token_count': selected_verb_token_count,
        'selected_verb_sentence_count': selected_verb_sentence_count,
        'switch_url': switch_url,
        'page': page,
        'per_page': per_page,
        'has_prev': has_prev,
        'has_next': has_next,
        'page_occurrence_start': page_occurrence_start,
        'page_occurrence_end': page_occurrence_end,
        'verbs_list_qs': verbs_list_qs,
    }

    return render_template('translit.html', enumerate=enumerate, **context)
