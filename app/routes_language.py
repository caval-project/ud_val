@app.context_processor
def utility_processor():
    """
    Expose small Jinja helpers to templates.

    update_query_params(...) preserves multi-value query parameters (e.g., repeated
    keys from multiple selects), and supports:
      - list/tuple: setlist
      - None:      remove key
      - scalar:    set key to value
    """
    def update_query_params(**kwargs):
        md = MultiDict(request.args)  # preserve repeated keys (doseq behavior)
        for k, v in kwargs.items():
            if isinstance(v, (list, tuple)):
                md.setlist(k, list(v))
            elif v is None:
                md.pop(k, None)
            else:
                md[k] = v
        return url_for(request.endpoint) + "?" + urlencode(list(md.lists()), doseq=True)

    return dict(update_query_params=update_query_params)


# NOTE: For production, always set SECRET_KEY via environment.
# The fallback is intentionally not secure and is only for local development.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')


@app.route('/', methods=['GET'])
def home():

    sort_order = request.args.get('sort', 'alphabetical')
    order_direction = request.args.get('order', 'asc')
    initial_param = request.args.get('initial')  # may be None, '', or a letter
    if initial_param is not None:
        session['initial'] = initial_param  # empty string clears the filter
    initial_letter = session.get('initial', '')
    search_query = request.args.get('search_query', '')
    language_search_query = request.args.get('language_search_query', '')
    english_search_query = request.args.get('english_search_query', '')
    selected_verb = request.args.get('selected_verb')
    selected_verb_gloss = request.args.get('selected_verb_gloss', '')

    # Source panel submission flag:
    # - If user submitted the panel and selected nothing -> show zero results (explicit intent).
    # - If panel never submitted -> treat as "no filter" (show all).
    source_submitted = 'source_checkbox_submitted' in request.args
    selected_sources = request.args.getlist('selected_source')

    search_submit = (
        request.args.get('search_submit') == '1'
        and (language_search_query or english_search_query)
    )

    if search_submit:
        session.pop('selected_verb', None)
        session.pop('selected_verb_gloss', None)
        session.pop('initial', None)
        initial_letter = ''
        selected_verb = None
        selected_verb_gloss = ''

    sel_v_arg = request.args.get('selected_verb')
    sel_g_arg = request.args.get('selected_verb_gloss')

    fresh_verb_search = (
        (language_search_query or english_search_query)
        and sel_v_arg is None
    )

    if fresh_verb_search:
        session.pop('selected_verb', None)
        session.pop('selected_verb_gloss', None)
        session.pop('initial', None)
        initial_letter = ''
        selected_verb = None
        selected_verb_gloss = ''

    init_arg = request.args.get('initial')

    if sel_v_arg is None and init_arg is not None:
        # If user clicked an initial (but didn't specify a verb), clear verb selection.
        session.pop('selected_verb', None)
        session.pop('selected_verb_gloss', None)
        selected_verb = None
        selected_verb_gloss = ''
    else:
        # URL args override the session. Empty string clears.
        if sel_v_arg is not None:
            session['selected_verb'] = sel_v_arg or None
        if sel_g_arg is not None:
            session['selected_verb_gloss'] = sel_g_arg or None

        selected_verb = session.get('selected_verb')
        selected_verb_gloss = session.get('selected_verb_gloss')

    # ----------------------------
    # Feature filters (multi-select)
    # ----------------------------
    selected_verbforms = request.args.getlist('verbform')
    selected_aspects = request.args.getlist('aspect')
    selected_cases = request.args.getlist('case_feature')  # "case" is a Python keyword
    selected_connegatives = request.args.getlist('Negation')
    selected_moods = request.args.getlist('mood')
    selected_numbers = request.args.getlist('number')
    selected_persons = request.args.getlist('person')
    selected_tenses = request.args.getlist('tense')
    selected_voices = request.args.getlist('voice')

    # Global reset handler: clears session-backed state and redirects to clean URL.
    if request.args.get('reset') == '1':
        for k in (
            'initial',
            't_initial',
            'selected_verb',
            'selected_verb_gloss',
            'language_search_query',
            'english_search_query',
        ):
            session.pop(k, None)
        return redirect(url_for('home'))

    # ----------------------------
    # Pagination (sentences list)
    # ----------------------------
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    try:
        per_page = max(1, min(200, int(request.args.get('per_page', 50))))  # defensive cap
    except ValueError:
        per_page = 50

    offset = (page - 1) * per_page

    # If the source panel was never submitted, interpret as "no filter".
    if not source_submitted:
        selected_sources = []

    # -------------------------------------------------------------------
    # SQL helper: constrain by selected verb lemma + optional gloss (sense)
    # -------------------------------------------------------------------
    def _add_selected_sense_filter(conditions, params, alias='verbs'):
        """
        Apply sense restriction: lemma always, gloss optionally.
        """
        if selected_verb:
            conditions.append(f"{alias}.lemma = :__sel_lemma")
            params['__sel_lemma'] = selected_verb
            if selected_verb_gloss:
                conditions.append(f"{alias}.gloss = :__sel_gloss")
                params['__sel_gloss'] = selected_verb_gloss

    def _add_search_filters(conditions, params, *, alias='verbs', include_search_filters=True):

        if not include_search_filters:
            return

        if language_search_query:
            conditions.append(f"{alias}.lemma = :__arm_search")
            params['__arm_search'] = language_search_query

        if english_search_query:
            conditions.append(f"LOWER({alias}.gloss) = LOWER(:__eng_search)")
            params['__eng_search'] = english_search_query.lower()

    # -------------------------------------------------------------------
    # Customizable: Source/language filtering based on sent_id patterns
    # -------------------------------------------------------------------
    def build_sources_condition(selected_sources, alias="sent_id"):
        """
        Return a SQL fragment restricting by source/language using sent_id patterns.

        IMPORTANT (customization):
        This mapping is corpus/project specific. Replace patterns and labels with
        your own source IDs. Ideally, long-term, store source metadata in a table
        instead of encoding it into sent_id strings.

        Current patterns:
          - German  -> sent_id LIKE '%hdt%'
          - Dutch   -> sent_id LIKE '%wiki%' OR '%WR-P-E-I%'
          - French  -> sent_id LIKE '%fr%'
          - English -> sent_id LIKE '%GUM%'
          - Greek   -> sent_id is a 5-digit number (REGEXP)
          - Arabic  -> "everything else" fallback
        """
        if source_submitted and len(selected_sources) == 0:
            return "0=1"  # explicit user intent: show nothing

        if not selected_sources:
            return None  # no filter applied

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

    # Flag used in template to show which script page we are on.
    is_translit_page = False

    selected_verb_url = None
    if selected_verb:
        if selected_verb_gloss:
            row = db.session.execute(
                text("""
                    SELECT url
                    FROM verbs
                    WHERE lemma = :selected_verb AND gloss = :selected_verb_gloss
                    LIMIT 1
                """),
                {'selected_verb': selected_verb, 'selected_verb_gloss': selected_verb_gloss}
            ).fetchone()
        else:
            row = db.session.execute(
                text("""
                    SELECT url
                    FROM verbs
                    WHERE lemma = :selected_verb
                    LIMIT 1
                """),
                {'selected_verb': selected_verb}
            ).fetchone()
        selected_verb_url = row.url if row else None

    # -------------------------------------------------------------------
    # Customizable: Script/transliteration mapping tables
    # -------------------------------------------------------------------
    # These maps are *language-specific* (currently Armenian).
    # To reuse this code for another language/script, replace these dictionaries
    # and the "token merging" rules below to match your orthography.
    latin_to_language = {
        'e=': 'է', "e'": 'ը', "t'": 'թ', 'z=': 'ժ', 'l=': 'ղ', 'c=': 'ճ',
        's=': 'շ', "c='": 'չ', 'j=': 'ջ', 'r=': 'ռ', "c'": 'ց', "p'": 'փ',
        "k'": 'ք', 'aw': 'աւ', 'a': 'ա', 'b': 'բ', 'g': 'գ', 'd': 'դ', 'e': 'ե',
        'z': 'զ', 'i': 'ի', 'l': 'լ', 'x': 'խ', 'c': 'ծ', 'k': 'կ', 'h': 'հ',
        'j': 'ձ', 'm': 'մ', 'y': 'յ', 'n': 'ն', 'o': 'ո', 'p': 'պ', 's': 'ս',
        'v': 'վ', 't': 'տ', 'r': 'ր', 'w': 'ւ', 'f': 'ֆ'
    }

    initial_map_translit_to_language = {
        'a': 'ա', 'b': 'բ', 'g': 'գ', 'd': 'դ', 'e': 'ե', 'z': 'զ',
        'ē': 'է', 'ǝ': 'ը', 'tʻ': 'թ', 'ž': 'ժ', 'i': 'ի', 'l': 'լ',
        'x': 'խ', 'c': 'ծ', 'k': 'կ', 'h': 'հ', 'j': 'ձ', 'ł': 'ղ',
        'č': 'ճ', 'm': 'մ', 'y': 'յ', 'n': 'ն', 'š': 'շ', 'o': 'ո',
        'čʻ': 'չ', 'ǰ': 'ջ', 'ṙ': 'ռ', 'cʻ': 'ց',
        'p': 'պ', 'v': 'վ', 't': 'տ', 'r': 'ր', 'w': 'ւ', 'pʻ': 'փ',
        'kʻ': 'ք', 'f': 'ֆ', 'aw': 'աւ'
    }
    initial_map_language_to_translit = {v: k for k, v in initial_map_translit_to_language.items()}

    # -------------------------------------------------------------------
    # Initial bar: compute available initials under current filters
    # -------------------------------------------------------------------
    def get_initials_under_filters(
        dependencies,
        selected_sources,
        initial_letter=None,
        *,
        include_selected_verb=True,
        include_search_filters=True
    ):
        """
        Return all initials (first character of verbs.lemma) that exist under the
        currently active filters.

        include_selected_verb/include_search_filters control whether this helper
        should be restricted to a single selected verb / exact-match searches.
        """
        conditions, params, joins = [], {}, ""
        active_dependencies = []

        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx
                active_dependencies.append(dep)
                alias = f'a{idx}'
                joins += (
                    f"JOIN arguments {alias} "
                    f"ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
                )
                if dep['deprel']:
                    conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                    params[f'deprel{idx}'] = dep['deprel']
                if dep['case_value']:
                    conditions.append(f"{alias}.case_value = :case_value{idx}")
                    params[f'case_value{idx}'] = dep['case_value']
                if dep['lemma']:
                    conditions.append(f"{alias}.lemma = :lemma{idx}")
                    params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    iidx = active_dependencies[i]['idx']
                    jidx = active_dependencies[j]['idx']
                    conditions.append(f"a{iidx}.token_id != a{jidx}.token_id")

        src_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if src_filter:
            conditions.append(src_filter)

        if initial_letter:
            conditions.append("verbs.lemma LIKE :__initial_letter__init")
            params["__initial_letter__init"] = f"{initial_letter}%"

        build_feature_conditions(conditions, params)

        if include_search_filters and language_search_query:
            conditions.append("verbs.lemma = :armq_init")
            params['armq_init'] = language_search_query
        if include_search_filters and english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:engq_init)")
            params['engq_init'] = english_search_query.lower()

        if include_selected_verb and selected_verb:
            conditions.append("verbs.lemma = :selv_init")
            params['selv_init'] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selv_gloss_init")
                params['selv_gloss_init'] = selected_verb_gloss

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        q = f"""
            SELECT DISTINCT SUBSTRING(verbs.lemma, 1, 1) AS initial
            FROM verbs
            {joins}
            WHERE {where_clause}
            ORDER BY initial
        """
        rows = db.session.execute(text(q), params).fetchall()
        return [row[0] for row in rows]

    # -------------------------------------------------------------------
    # Feature conditions builder 
    # -------------------------------------------------------------------
    def build_feature_conditions_for_alias(conditions, params):
        """
        Same logic as build_feature_conditions(), but applying conditions on alias "v".
        """
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

    # -------------------------------------------------------------------
    # Customizable: Latinized input normalization (project-specific)
    # -------------------------------------------------------------------
    def convert_latinized_to_language(input_str):
        """
        Convert a Latinized input string into the target script based on the mapping.

        Customization:
        - The mapping currently expects Armenian-specific digraphs and symbols.
        - Replace latin_to_language keys/values to support other orthographies.
        """
        mapping_keys = sorted(latin_to_language.keys(), key=lambda x: -len(x))
        output_str = ''
        i = 0
        while i < len(input_str):
            matched = False
            for key in mapping_keys:
                if input_str[i:i + len(key)] == key:
                    output_str += latin_to_language[key]
                    i += len(key)
                    matched = True
                    break
            if not matched:
                output_str += input_str[i]
                i += 1
        return output_str

    # -------------------------------------------------------------------
    # Dependency filter UI state
    # -------------------------------------------------------------------
    dependencies = []
    max_dependencies = 5  # Customization: increase/decrease number of dependency filter blocks
    dependency_visible_flags = []

    for idx in range(max_dependencies):
        if idx == 0:
            dep = {
                'deprel': request.args.get('syntactic_relation') or None,
                'case_value': request.args.get('case_value') or None,
                'lemma': request.args.get('case_dependant_lemma') or None,
            }
            visible = True  # first dependency block is always visible
        else:
            dep = {
                'deprel': request.args.get(f'co_occurring_deprel_{idx + 1}') or None,
                'case_value': request.args.get(f'co_occurring_case_value_{idx + 1}') or None,
                'lemma': request.args.get(f'co_occurring_lemma_{idx + 1}') or None,
            }
            visible = request.args.get(f'dependency{idx + 1}_visible', 'false') == 'true'

        dependency_visible_flags.append(visible)
        dependencies.append(dep)

    last_visible_dependency_index = -1
    for idx, visible in enumerate(dependency_visible_flags):
        if visible:
            last_visible_dependency_index = idx

    dependency_has_selection = []
    for dep in dependencies:
        dependency_has_selection.append(any([dep['deprel'], dep['case_value'], dep['lemma']]))

    # Preferred display ordering for dependency relations (project/UI choice).
    desired_deprel_order = [
        'nsubj', 'nsubj:pass', 'nsubj:caus',
        'csubj', 'csubj:caus', 'csubj:pass', 'obj', 'ccomp',
        'iobj', 'obl', 'obl:agent', 'obl:arg', 'aux', 'aux:caus'
    ]

    def sort_deprels(deprels, desired_order):
        desired_set = set(desired_order)
        in_order = [deprel for deprel in desired_order if deprel in deprels]
        not_in_order = sorted([deprel for deprel in deprels if deprel not in desired_set])
        return in_order + not_in_order

    dependencies_options = []

    # -------------------------------------------------------------------
    # Dynamic options generation for each dependency block
    # -------------------------------------------------------------------
    def build_common_components(dependencies, current_level, selected_verb, selected_sources):
        """
        Build FROM/JOIN/WHERE components shared by all "dynamic option" queries for a
        given dependency block (current_level).

        NOTE: This is performance-sensitive: it is called multiple times to populate
        dropdowns. Keep it deterministic and avoid extra queries here.
        """
        conditions = []
        params = {}
        joins = "FROM verbs v\n"
        select_alias = f'a{current_level}'

        _add_selected_sense_filter(conditions, params, alias='v')
        _add_search_filters(conditions, params, alias='v', include_search_filters=(selected_verb is None))

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma') or idx == current_level:
                dep_copy = dep.copy()
                dep_copy['idx'] = idx
                active_dependencies.append(dep_copy)

        if selected_verb:
            conditions.append("v.lemma = :selected_verb")
            params['selected_verb'] = selected_verb

        if initial_letter and not selected_verb:
            conditions.append("v.lemma LIKE :__initial_letter_v")
            params["__initial_letter_v"] = f"{initial_letter}%"

        source_filter = build_sources_condition(selected_sources, alias="v.sent_id")
        if source_filter:
            conditions.append(f"({source_filter})")

        build_feature_conditions_for_alias(conditions, params)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"

            if idx != current_level:
                if dep.get('deprel'):
                    conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                    params[f'deprel{idx}'] = dep['deprel']
                if dep.get('case_value'):
                    conditions.append(f"{alias}.case_value = :case_value{idx}")
                    params[f'case_value{idx}'] = dep['case_value']
                if dep.get('lemma'):
                    conditions.append(f"{alias}.lemma = :lemma{idx}")
                    params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    alias_i = f'a{active_dependencies[i]["idx"]}'
                    alias_j = f'a{active_dependencies[j]["idx"]}'
                    conditions.append(f"{alias_i}.token_id != {alias_j}.token_id")

        return joins, conditions, params, select_alias

    def get_dynamic_values(column, dependencies, current_level, selected_verb, selected_sources, common_components, excluded_combinations):
        """
        Fetch distinct values for a specific column (dep_rel/case_value/lemma)
        for the dependency block at current_level, under all currently active filters.
        """
        joins, base_conditions, base_params, select_alias = common_components
        conditions = list(base_conditions)
        params = base_params.copy()

        current_dep = dependencies[current_level] if current_level < len(dependencies) else {}

        if current_dep.get('deprel') and column != 'dep_rel':
            conditions.append(f"{select_alias}.dep_rel = :current_deprel")
            params['current_deprel'] = current_dep['deprel']
        if current_dep.get('case_value') and column != 'case_value':
            conditions.append(f"{select_alias}.case_value = :current_case_value")
            params['current_case_value'] = current_dep['case_value']
        if current_dep.get('lemma') and column != 'lemma':
            conditions.append(f"{select_alias}.lemma = :current_lemma")
            params['current_lemma'] = current_dep['lemma']

        where_clause = ' AND '.join(conditions) if conditions else '1=1'
        query = f"""
            SELECT DISTINCT {select_alias}.{column}
            {joins}
            WHERE {where_clause}
            ORDER BY {select_alias}.{column}
        """
        result = db.session.execute(text(query), params).fetchall()
        return [row[0] for row in result] if result else []

    dependencies_options = []
    excluded_combinations = []

    for idx in range(len(dependencies)):
        common_components = build_common_components(dependencies, idx, selected_verb, selected_sources)

        deprels = get_dynamic_values('dep_rel', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)
        case_values = get_dynamic_values('case_value', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)
        lemmas = get_dynamic_values('lemma', dependencies, idx, selected_verb, selected_sources, common_components, excluded_combinations)

        deprels_list = [dr for dr in deprels if dr is not None]
        case_values_list = [cv for cv in case_values if cv is not None]
        lemmas_list = [lemma for lemma in lemmas if lemma is not None]

        dependencies_options.append({
            'deprels': sort_deprels(deprels_list, desired_deprel_order),
            'case_values': sorted(case_values_list),
            'lemmas': sorted(lemmas_list),
        })

        current_dep = dependencies[idx] if idx < len(dependencies) else {}
        if current_dep.get('deprel') or current_dep.get('case_value') or current_dep.get('lemma'):
            excluded_combinations.append(current_dep)

    has_next_dependency_options = []
    for level in range(len(dependencies)):
        if level + 1 < len(dependencies):
            next_level = level + 1
            common_components_next = build_common_components(dependencies, next_level, selected_verb, selected_sources)

            deprels = get_dynamic_values('dep_rel', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)
            case_values = get_dynamic_values('case_value', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)
            lemmas = get_dynamic_values('lemma', dependencies, next_level, selected_verb, selected_sources, common_components_next, excluded_combinations)

            has_next_dependency_options.append(any([deprels, case_values, lemmas]))
        else:
            has_next_dependency_options.append(False)

    # -------------------------------------------------------------------
    # Dynamic feature values under current filters (sets per feature)
    # -------------------------------------------------------------------
    def get_dynamic_feature_values(dependencies, selected_sources):
        """
        Return distinct possible values for each verb feature under current filters.
        Used to render feature filter dropdowns that stay consistent with constraints.
        """
        conditions = []
        params = {}
        joins = "FROM verbs v\n"

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx
                active_dependencies.append(dep)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.case_value = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        source_filter = build_sources_condition(selected_sources, alias="v.sent_id")
        if source_filter:
            conditions.append(source_filter)

        if initial_letter and not selected_verb:
            conditions.append("v.lemma LIKE :__initial_letter_feats")
            params["__initial_letter_feats"] = f"{initial_letter}%"

        _add_selected_sense_filter(conditions, params, alias='v')
        _add_search_filters(conditions, params, alias='v', include_search_filters=(selected_verb is None))

        # Apply already-chosen feature filters on v.* so options remain consistent
        if selected_verbforms:
            conditions.append("v.VerbForm IN :verbforms_list"); params["verbforms_list"] = tuple(selected_verbforms)
        if selected_aspects:
            conditions.append("v.Aspect IN :aspects_list"); params["aspects_list"] = tuple(selected_aspects)
        if selected_cases:
            conditions.append("v.Case IN :cases_list"); params["cases_list"] = tuple(selected_cases)
        if selected_connegatives:
            conditions.append("v.Connegative IN :conneg_list"); params["conneg_list"] = tuple(selected_connegatives)
        if selected_moods:
            conditions.append("v.Mood IN :moods_list"); params["moods_list"] = tuple(selected_moods)
        if selected_numbers:
            conditions.append("v.Number IN :numbers_list"); params["numbers_list"] = tuple(selected_numbers)
        if selected_persons:
            conditions.append("v.Person IN :persons_list"); params["persons_list"] = tuple(selected_persons)
        if selected_tenses:
            conditions.append("v.Tense IN :tenses_list"); params["tenses_list"] = tuple(selected_tenses)
        if selected_voices:
            conditions.append("v.Voice IN :voices_list"); params["voices_list"] = tuple(selected_voices)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT
               GROUP_CONCAT(DISTINCT v.VerbForm)     AS all_verbforms,
               GROUP_CONCAT(DISTINCT v.Aspect)       AS all_aspects,
               GROUP_CONCAT(DISTINCT v.Case)         AS all_cases,
               GROUP_CONCAT(DISTINCT v.Connegative)  AS all_negations,
               GROUP_CONCAT(DISTINCT v.Mood)         AS all_moods,
               GROUP_CONCAT(DISTINCT v.Number)       AS all_numbers,
               GROUP_CONCAT(DISTINCT v.Person)       AS all_persons,
               GROUP_CONCAT(DISTINCT v.Tense)        AS all_tenses,
               GROUP_CONCAT(DISTINCT v.Voice)        AS all_voices
            {joins}
            WHERE {where_clause}
            LIMIT 1
        """
        result = db.session.execute(text(query), params).fetchone()

        def str_to_set(s):
            return set(s.split(',')) if s else set()

        if not result:
            return {k: set() for k in ("VerbForm", "Aspect", "Case", "Negation", "Mood", "Number", "Person", "Tense", "Voice")}

        return {
            "VerbForm": str_to_set(result.all_verbforms),
            "Aspect":   str_to_set(result.all_aspects),
            "Case":     str_to_set(result.all_cases),
            "Negation": str_to_set(result.all_negations),
            "Mood":     str_to_set(result.all_moods),
            "Number":   str_to_set(result.all_numbers),
            "Person":   str_to_set(result.all_persons),
            "Tense":    str_to_set(result.all_tenses),
            "Voice":    str_to_set(result.all_voices),
        }

    # -------------------------------------------------------------------
    # Feature filters builder 
    # -------------------------------------------------------------------
    def build_feature_conditions(conditions, params):
        """
        Apply chosen verb features to queries that use the "verbs" table without aliasing.
        Only adds constraints for features that the user actually selected.
        """
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

    # -------------------------------------------------------------------
    # Verbs list query: lemma + gloss + frequency under current filters
    # -------------------------------------------------------------------
    def get_verbs_with_frequencies(
        dependencies,
        sort_order,
        order_direction,
        initial_letter,
        language_search_query,
        english_search_query,
        selected_verb=None,
        selected_sources=None
    ):
        if selected_sources is None:
            selected_sources = []

        conditions = []
        params = {}
        joins = ''

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx
                active_dependencies.append(dep)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.case_value = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        build_feature_conditions(conditions, params)

        if initial_letter and not selected_verb:
            conditions.append("verbs.lemma LIKE :initial_letter")
            params['initial_letter'] = f"{initial_letter}%"

        if language_search_query:
            conditions.append("verbs.lemma = :language_search_query")
            params['language_search_query'] = language_search_query

        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:english_search_query)")
            params['english_search_query'] = english_search_query.lower()

        source_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if source_filter:
            conditions.append(source_filter)

        if selected_verb:
            conditions.append("verbs.lemma = :selected_verb")
            params['selected_verb'] = selected_verb

        where_clause = ' AND '.join(conditions) if conditions else '1=1'
        query = f"""
            SELECT verbs.lemma, verbs.gloss,
                   COUNT(DISTINCT verbs.token_id, verbs.sent_id) AS frequency
            FROM verbs
            {joins}
            WHERE {where_clause}
            GROUP BY verbs.lemma, verbs.gloss
        """

        if sort_order == 'frequency':
            query += f" ORDER BY frequency {'ASC' if order_direction == 'asc' else 'DESC'}"
        else:
            query += f" ORDER BY verbs.lemma {'ASC' if order_direction == 'asc' else 'DESC'}"

        return db.session.execute(text(query), params).fetchall()

    verbs_result = get_verbs_with_frequencies(
        dependencies,
        sort_order,
        order_direction,
        initial_letter,
        language_search_query,
        english_search_query,
        selected_verb,
        selected_sources=selected_sources
    )

    verbs_with_frequencies = [{'lemma': row[0], 'gloss': row[1], 'frequency': row[2]} for row in verbs_result]
    total_verb_count = len(verbs_with_frequencies)
    total_occurrence_count = sum(int(v['frequency']) for v in verbs_with_frequencies)

    # -------------------------------------------------------------------
    # Total sentence count under filters (for list view / stats)
    # -------------------------------------------------------------------
    def get_total_sentence_count(
        dependencies,
        initial_letter,
        language_search_query,
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

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx
                active_dependencies.append(dep)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += f"JOIN arguments {alias} ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.case_value = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        if initial_letter and not selected_verb:
            conditions.append("verbs.lemma LIKE :initial_letter")
            params['initial_letter'] = f"{initial_letter}%"

        if language_search_query:
            conditions.append("verbs.lemma = :language_search_query")
            params['language_search_query'] = language_search_query

        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:english_search_query)")
            params['english_search_query'] = english_search_query.lower()

        source_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if source_filter:
            conditions.append(source_filter)

        if selected_verb:
            conditions.append("verbs.lemma = :selected_verb")
            params['selected_verb'] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss

        build_feature_conditions(conditions, params)

        where_clause = ' AND '.join(conditions) if conditions else '1=1'
        query = f"""
            SELECT COUNT(DISTINCT verbs.sent_id) AS total_sentences
            FROM verbs
            {joins}
            WHERE {where_clause}
        """
        result = db.session.execute(text(query), params).fetchone()
        return result.total_sentences if result else 0

    total_sentence_count = get_total_sentence_count(
        dependencies,
        initial_letter,
        language_search_query,
        english_search_query,
        selected_verb,
        selected_verb_gloss,
        selected_sources=selected_sources
    )

    # -------------------------------------------------------------------
    # Total sentence count under current filters
    # -------------------------------------------------------------------
    def get_total_sentence_count(
        dependencies,
        initial_letter,
        language_search_query,
        english_search_query,
        selected_verb=None,
        selected_verb_gloss=None,
        selected_sources=None
    ):
        """
        Count DISTINCT sentences (verbs.sent_id) that satisfy all active constraints.
        """
        if selected_sources is None:
            selected_sources = []

        conditions = []
        params = {}
        joins = ''

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep['deprel'] or dep['case_value'] or dep['lemma']:
                dep['idx'] = idx
                active_dependencies.append(dep)

        for dep in active_dependencies:
            idx = dep['idx']
            alias = f'a{idx}'
            joins += (
                f"JOIN arguments {alias} "
                f"ON verbs.token_id = {alias}.head_id AND verbs.sent_id = {alias}.sent_id\n"
            )
            if dep['deprel']:
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f'deprel{idx}'] = dep['deprel']
            if dep['case_value']:
                conditions.append(f"{alias}.case_value = :case_value{idx}")
                params[f'case_value{idx}'] = dep['case_value']
            if dep['lemma']:
                conditions.append(f"{alias}.lemma = :lemma{idx}")
                params[f'lemma{idx}'] = dep['lemma']

        # If multiple dependencies are active, enforce that they refer to different argument tokens.
        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        # Alphabetical filter applies only to verbs list mode (not when a verb is selected).
        if initial_letter and not selected_verb:
            conditions.append("verbs.lemma LIKE :initial_letter")
            params['initial_letter'] = f"{initial_letter}%"

        # Exact-match search filters
        if language_search_query:
            conditions.append("verbs.lemma = :language_search_query")
            params['language_search_query'] = language_search_query

        if english_search_query:
            conditions.append("LOWER(verbs.gloss) = LOWER(:english_search_query)")
            params['english_search_query'] = english_search_query.lower()

        # Source/language selection filter (sent_id pattern-based mapping).
        source_filter = build_sources_condition(selected_sources, alias="verbs.sent_id")
        if source_filter:
            conditions.append(source_filter)

        # Selected verb (+ optional selected gloss/sense)
        if selected_verb:
            conditions.append("verbs.lemma = :selected_verb")
            params['selected_verb'] = selected_verb
            if selected_verb_gloss:
                conditions.append("verbs.gloss = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss

        # Verb feature filters (VerbForm, Aspect, Case, etc.)
        build_feature_conditions(conditions, params)

        where_clause = ' AND '.join(conditions) if conditions else '1=1'
        query = f"""
            SELECT COUNT(DISTINCT verbs.sent_id) AS total_sentences
            FROM verbs
            {joins}
            WHERE {where_clause}
        """
        result = db.session.execute(text(query), params).fetchone()
        return result.total_sentences if result else 0

    total_sentence_count = get_total_sentence_count(
        dependencies, initial_letter, language_search_query, english_search_query,
        selected_verb, selected_verb_gloss, selected_sources=selected_sources
    )

    # -------------------------------------------------------------------
    # Dependency tree helper 
    # -------------------------------------------------------------------
    def collect_dependency_tree(word_map, root_token_id, allowed_dep_rels):
        """
        Collect token_ids in the dependency subtree rooted at root_token_id.

        Customization:
        - allowed_dep_rels controls which relations to traverse.
        - 'case' is always traversed additionally to include case-markers.
        """
        dependency_tree_token_ids = set()
        stack = [root_token_id]
        while stack:
            current_token_id = stack.pop()
            dependency_tree_token_ids.add(current_token_id)
            for word in word_map.values():
                if word['head_id'] == current_token_id and word['token_id'] not in dependency_tree_token_ids:
                    if word['dep_rel'] in allowed_dep_rels or word['dep_rel'] == 'case':
                        stack.append(word['token_id'])
        return dependency_tree_token_ids

    # -------------------------------------------------------------------
    # Sentence filtering core for the "sentences page" (selected verb mode)
    # -------------------------------------------------------------------
    def _build_sentence_where_for_selected_verb(selected_verb, selected_verb_gloss, dependencies, selected_sources):
        """
        Build the JOINs, WHERE conditions, and params that define the *sentence set*
        for a selected verb (+ optional sense) under the currently active filters.

        This is used by:
          - totals computation (total tokens / total sentences)
          - selecting which sent_ids belong to the current page

        IMPORTANT:
        This function does *not* fetch words/arguments; it only defines the filtered set.
        """
        params = {}
        conditions = []
        joins = "JOIN verbs v ON s.sent_id = v.sent_id\n"

        # If no selected verb, sentences page is undefined -> return empty result constraint.
        if not selected_verb:
            return joins, ["0=1"], params

        # Selected verb lemma + optional gloss
        conditions.append("v.lemma = :sel_v")
        params["sel_v"] = selected_verb
        if selected_verb_gloss:
            conditions.append("v.gloss = :sel_vg")
            params["sel_vg"] = selected_verb_gloss

        # Source/language filter (note alias uses sentences table here)
        src_filter = build_sources_condition(selected_sources, alias="s.sent_id")
        if src_filter:
            conditions.append(src_filter)

        # Dependency constraints: LEFT JOIN arguments for each active dependency block.
        active = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma'):
                active.append((idx, dep))

        for idx, dep in active:
            alias = f'a{idx}'
            joins += (
                f"LEFT JOIN arguments {alias} "
                f"ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"
            )
            if dep.get('deprel'):
                conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                params[f"deprel{idx}"] = dep['deprel']
            if dep.get('case_value'):
                conditions.append(f"{alias}.case_value = :case_value{idx}")
                params[f"case_value{idx}"] = dep['case_value']
            if dep.get('lemma'):
                conditions.append(f"{alias}.lemma = :lemma{idx}")
                params[f"lemma{idx}"] = dep['lemma']

        # Multiple active dependencies must refer to different argument tokens.
        if len(active) >= 2:
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    iidx, jidx = active[i][0], active[j][0]
                    conditions.append(f"a{iidx}.token_id != a{jidx}.token_id")

        # Verb feature filters on alias v.*
        if selected_verbforms:      conditions.append("v.VerbForm IN :vf");       params["vf"] = tuple(selected_verbforms)
        if selected_aspects:        conditions.append("v.Aspect   IN :asp");      params["asp"] = tuple(selected_aspects)
        if selected_cases:          conditions.append("v.Case     IN :cas");      params["cas"] = tuple(selected_cases)
        if selected_connegatives:   conditions.append("v.Connegative IN :neg");   params["neg"] = tuple(selected_connegatives)
        if selected_moods:          conditions.append("v.Mood     IN :mood");     params["mood"] = tuple(selected_moods)
        if selected_numbers:        conditions.append("v.Number   IN :num");      params["num"] = tuple(selected_numbers)
        if selected_persons:        conditions.append("v.Person   IN :per");      params["per"] = tuple(selected_persons)
        if selected_tenses:         conditions.append("v.Tense    IN :ten");      params["ten"] = tuple(selected_tenses)
        if selected_voices:         conditions.append("v.Voice    IN :voi");      params["voi"] = tuple(selected_voices)

        return joins, conditions, params

    # -------------------------------------------------------------------
    # Token-window pagination configuration
    # -------------------------------------------------------------------
    # Customization:
    # - Set PAGE_TOKEN_SIZE to change the window size used for paging.
    # - This is NOT "sentences per page", but "selected verb occurrences per page".
    PAGE_TOKEN_SIZE = 50

    def get_selected_verb_totals_and_page_ids(
        selected_verb,
        selected_verb_gloss,
        dependencies,
        selected_sources,
        *,
        page,
        per_page,
        offset
    ):
        """
        Returns:
          total_sentences, total_tokens, prev_tokens_cum, page_sent_ids, page_token_total

        Paging model:
          - Each page covers a fixed window of N selected-verb occurrences (PAGE_TOKEN_SIZE).
          - A sentence may contain multiple occurrences; we compute per-sentence hits and
            slice the cumulative stream of hits into page windows.
        """
        joins, conds, params = _build_sentence_where_for_selected_verb(
            selected_verb, selected_verb_gloss, dependencies, selected_sources
        )
        where_clause = " AND ".join(conds) if conds else "1=1"

        # 1) Total sentences + total selected-verb token hits across those sentences
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
              (SELECT COUNT(*)                      FROM filtered) AS total_sentences,
              (SELECT COALESCE(SUM(token_hits), 0)  FROM filtered) AS total_tokens
            ;
        """
        row = db.session.execute(text(q_totals), params).fetchone()
        total_sentences = int(row.total_sentences or 0)
        total_tokens = int(row.total_tokens or 0)

        # 2) Define token window boundaries for this page (0-based offset)
        token_offset = max(0, (page - 1) * PAGE_TOKEN_SIZE)
        window_end = token_offset + PAGE_TOKEN_SIZE

        if token_offset >= total_tokens:
            return total_sentences, total_tokens, token_offset, [], 0

        # 3) Fetch per-sentence token hits in deterministic order
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

        # 4) Walk the cumulative sum to collect sentences intersecting the current window
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
                add = min(hits, room)
                page_token_total += add
                cum += hits
            i += 1

        prev_tokens_cum = token_offset
        return total_sentences, total_tokens, prev_tokens_cum, page_sent_ids, page_token_total

    # -------------------------------------------------------------------
    # Tooltip formatting
    # -------------------------------------------------------------------
    def format_tooltip(word):
        """
        Produce the tooltip content from a token's gloss + morphological feats.

        Note:
        - We replace spaces with NBSP in gloss to prevent awkward line breaks.
        """
        gloss_part = word['gloss'].replace(" ", "\u00A0") if word['gloss'] else ""
        feat_part = word['feat'] if word['feat'] else ""
        return f"{gloss_part}.{feat_part}" if gloss_part and feat_part else gloss_part or feat_part

    # -------------------------------------------------------------------
    # Sentence payload builder (batched DB fetch + in-memory assembly)
    # -------------------------------------------------------------------
    def get_sentences(selected_verb, selected_verb_gloss, dependencies, selected_sources=None):
        """
        Fetch sentence data (sentences + words + arguments) and assemble the per-sentence
        structures used by the UI.

        Performance notes:
        - This implementation batches:
            * sentence headers
            * selected verb tokens
            * words (for all sent_ids)
            * arguments (for all sent_ids)
          and then assembles everything in memory.
        """
        if not selected_verb:
            return []
        if selected_sources is None:
            selected_sources = []

        conditions = ["v.lemma = :selected_verb"]
        params = {'selected_verb': selected_verb}

        source_filter = build_sources_condition(selected_sources, alias="s.sent_id")
        if source_filter:
            conditions.append(source_filter)

        if selected_verb_gloss:
            conditions.append("v.gloss = :selected_verb_gloss")
            params['selected_verb_gloss'] = selected_verb_gloss

        joins = ''

        active_dependencies = []
        for idx, dep in enumerate(dependencies):
            if dep.get('deprel') or dep.get('case_value') or dep.get('lemma'):
                dep = {**dep, 'idx': idx}
                active_dependencies.append(dep)
                alias = f'a{idx}'
                joins += (
                    f"LEFT JOIN arguments {alias} "
                    f"ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"
                )
                if dep.get('deprel'):
                    conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                    params[f'deprel{idx}'] = dep['deprel']
                if dep.get('case_value'):
                    conditions.append(f"{alias}.case_value = :case_value{idx}")
                    params[f'case_value{idx}'] = dep['case_value']
                if dep.get('lemma'):
                    conditions.append(f"{alias}.lemma = :lemma{idx}")
                    params[f'lemma{idx}'] = dep['lemma']

        if len(active_dependencies) >= 2:
            for i in range(len(active_dependencies)):
                for j in range(i + 1, len(active_dependencies)):
                    idx_i = active_dependencies[i]['idx']
                    idx_j = active_dependencies[j]['idx']
                    conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

        # Feature filters on v.*
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

        where_clause = ' AND '.join(conditions)

        # 1) Sentence headers
        sentences_basic_info = db.session.execute(text(f"""
            SELECT DISTINCT s.sent_id, s.text, s.translated_text
            FROM sentences s
            JOIN verbs v ON s.sent_id = v.sent_id
            {joins}
            WHERE {where_clause}
            GROUP BY s.sent_id, s.text, s.translated_text
        """), params).fetchall()

        if not sentences_basic_info:
            return []

        # 2) Selected verb tokens per sentence
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

        # 3) Batch fetch all words for these sent_ids
        sent_ids = [row[0] for row in sentences_basic_info]
        safe_sent_ids = tuple(sent_ids) if sent_ids else tuple([-1])

        words_all = db.session.execute(text("""
            SELECT w.sent_id, w.token_id, w.form, CAST(w.feat AS CHAR), w.gloss, w.head_id, w.dep_rel, w.pos
            FROM words w
            WHERE w.sent_id IN :sent_ids
            ORDER BY w.sent_id, w.token_id
        """), {"sent_ids": safe_sent_ids}).fetchall()

        words_by_sent = defaultdict(list)
        for row in words_all:
            words_by_sent[row.sent_id].append(row)

        # 4) Batch fetch all arguments for these sent_ids
        args_all = db.session.execute(text("""
            SELECT a.sent_id, a.head_id, a.token_id, a.dep_rel, a.cdep_token_id, a.second_cdep_token_id, a.fdep_token_id
            FROM arguments a
            WHERE a.sent_id IN :sent_ids
        """), {"sent_ids": safe_sent_ids}).fetchall()

        args_by_sent = defaultdict(list)
        for a in args_all:
            args_by_sent[a.sent_id].append(a)

        # 5) Assemble sentence objects
        sentences = []

        for sent_id, text_val, translated_text in sentences_basic_info:
            words = words_by_sent.get(sent_id, [])
            if not words:
                continue

            # Build token map
            word_map = {}
            for w in words:
                feat = w[3] if w[3] != 'None' else None
                if feat:
                    feat_parts = feat.split('|')
                    feat_parts = list(set(feat_parts))  # deduplicate
                    feat = '|'.join(feat_parts)

                token_id_int = int(w[1])
                head_id_int = int(w[5]) if w[5] is not None else None

                word_map[token_id_int] = {
                    'token_id': token_id_int,
                    'form': w[2] if w[2] is not None else '',
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

            selected_token_ids = verb_token_ids_per_sent.get(sent_id, [])
            if not selected_token_ids:
                continue

            relations = []

            # Keep only arguments whose head is one of the selected verb tokens in this sentence.
            relevant_args = []
            heads = set(selected_token_ids)
            for a in args_by_sent.get(sent_id, []):
                if int(a.head_id) in heads:
                    relevant_args.append(a)

            # Mark selected verb tokens
            for verb_token_id in selected_token_ids:
                if verb_token_id in word_map:
                    word_map[verb_token_id]['is_selected_verb'] = True

            # Create relation edges for BRAT / visualization
            for arg in relevant_args:
                head_id = int(arg.head_id)
                arg_token_id = int(arg.token_id) if arg.token_id is not None else None
                cdep_token_id = int(arg.cdep_token_id) if arg.cdep_token_id is not None else None
                second_cdep_token_id = int(arg.second_cdep_token_id) if arg.second_cdep_token_id is not None else None
                fdep_token_id = int(arg.fdep_token_id) if arg.fdep_token_id is not None else None
                dep_rel = arg.dep_rel or 'argument'

                if arg_token_id and arg_token_id in word_map:
                    word_map[arg_token_id]['is_argument'] = True
                    relations.append({'from': head_id, 'to': arg_token_id, 'dep_rel': dep_rel})

                if cdep_token_id and cdep_token_id in word_map:
                    word_map[cdep_token_id]['is_case_dependent'] = True
                    relations.append({'from': arg_token_id if arg_token_id else head_id, 'to': cdep_token_id, 'dep_rel': 'case_dependency'})

                if second_cdep_token_id and second_cdep_token_id in word_map:
                    word_map[second_cdep_token_id]['is_case_dependent'] = True
                    relations.append({'from': arg_token_id if arg_token_id else head_id, 'to': second_cdep_token_id, 'dep_rel': 'case_dependency'})

                if fdep_token_id and fdep_token_id in word_map:
                    word_map[fdep_token_id]['is_fixed_dependent'] = True
                    if second_cdep_token_id:
                        relations.append({'from': second_cdep_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                    elif cdep_token_id:
                        relations.append({'from': cdep_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                    elif arg_token_id:
                        relations.append({'from': arg_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                    else:
                        relations.append({'from': head_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})

            # ------------------------------------------------------------
            # Token merging for surface display (orthography-specific)
            # ------------------------------------------------------------
            # Customization:
            # - These sets are language/script specific.
            # - Replace for another language (or remove merging entirely).
            original_words = list(word_map.values())
            merged_words = []

            tokens_attach_to_next = {'յ', 'զ', 'ց', 'չ', 'Յ', 'Զ', 'Ց', 'Չ'}
            tokens_attach_to_prev = {'ս', 'դ', 'ն', '՝', '.', ':', ','}
            special_attach_tokens = {'՞', '՛'}
            vowels = {'ա', 'ե', 'է', 'ը', 'ի', 'օ', 'ու', 'ո', 'Է'}

            i = 0
            while i < len(original_words):
                current_token = original_words[i]
                current_form = current_token['form']
                current_token_ids = [str(current_token['token_id'])]
                current_attrs = current_token.copy()
                current_attrs['token_id'] = '_'.join(current_token_ids)

                tokens_info = current_token.get('tokens_info', [{'gloss': current_token.get('gloss'), 'feat': current_token.get('feat')}])
                i += 1

                # Special punctuation attachment inside previous token’s vowel position.
                if current_form in special_attach_tokens and merged_words:
                    last_word = merged_words.pop()
                    last_form = last_word['form']
                    vowel_indices = [idx for idx, char in enumerate(last_form) if char in vowels]
                    insert_pos = (vowel_indices[-1] + 1) if vowel_indices else len(last_form)

                    new_form = last_form[:insert_pos] + current_form + last_form[insert_pos:]
                    last_word['form'] = new_form
                    last_word['tokens_info'] += tokens_info

                    last_word['is_selected_verb'] = last_word['is_selected_verb'] or current_attrs['is_selected_verb']
                    last_word['is_argument'] = last_word['is_argument'] or current_attrs['is_argument']
                    last_word['is_case_dependent'] = last_word['is_case_dependent'] or current_attrs['is_case_dependent']
                    last_word['is_fixed_dependent'] = last_word['is_fixed_dependent'] or current_attrs['is_fixed_dependent']

                    last_word['token_id'] = str(last_word['token_id']) + '_' + str(current_attrs['token_id'])

                    tooltip_parts = []
                    for token_info in last_word['tokens_info']:
                        gloss = token_info.get('gloss')
                        feat = token_info.get('feat')
                        if gloss and feat:
                            tooltip_part = f"{gloss}.{feat}"
                        elif gloss:
                            tooltip_part = gloss
                        elif feat:
                            tooltip_part = feat
                        else:
                            tooltip_part = ''
                        if tooltip_part:
                            tooltip_parts.append(tooltip_part)

                    last_word['gloss'] = '='.join(tooltip_parts)
                    merged_words.append(last_word)
                    continue

                # Attach suffix-like tokens to previous token.
                while current_form in tokens_attach_to_prev and merged_words:
                    last_word = merged_words.pop()
                    current_form = last_word['form'] + current_form
                    current_token_ids = last_word['token_id'].split('_') + current_token_ids
                    tokens_info = last_word['tokens_info'] + tokens_info

                    current_attrs['is_selected_verb'] = current_attrs['is_selected_verb'] or last_word['is_selected_verb']
                    current_attrs['is_argument'] = current_attrs['is_argument'] or last_word['is_argument']
                    current_attrs['is_case_dependent'] = current_attrs['is_case_dependent'] or last_word['is_case_dependent']
                    current_attrs['is_fixed_dependent'] = current_attrs['is_fixed_dependent'] or last_word['is_fixed_dependent']
                    current_attrs['pos'] = current_attrs['pos'] or last_word['pos']

                # Attach prefix-like tokens to next token.
                while current_form in tokens_attach_to_next and i < len(original_words):
                    next_token = original_words[i]
                    current_form += next_token['form']
                    current_token_ids.append(str(next_token['token_id']))
                    tokens_info += next_token.get('tokens_info', [{'gloss': next_token.get('gloss'), 'feat': next_token.get('feat')}])

                    current_attrs['is_selected_verb'] = current_attrs['is_selected_verb'] or next_token['is_selected_verb']
                    current_attrs['is_argument'] = current_attrs['is_argument'] or next_token['is_argument']
                    current_attrs['is_case_dependent'] = current_attrs['is_case_dependent'] or next_token['is_case_dependent']
                    current_attrs['is_fixed_dependent'] = current_attrs['is_fixed_dependent'] or next_token['is_fixed_dependent']
                    current_attrs['pos'] = current_attrs['pos'] or next_token['pos']

                    i += 1
                    if next_token['form'] not in tokens_attach_to_next:
                        break

                # Build merged tooltip string from all merged token pieces.
                tooltip_parts = []
                for token_info in tokens_info:
                    gloss = token_info.get('gloss')
                    feat = token_info.get('feat')
                    if gloss and feat:
                        tooltip_part = f"{gloss}.{feat}"
                    elif gloss:
                        tooltip_part = gloss
                    elif feat:
                        tooltip_part = feat
                    else:
                        tooltip_part = ''
                    tooltip_parts.append(tooltip_part)

                tooltip_parts = [part for part in tooltip_parts if part]
                combined_tooltip = '='.join(tooltip_parts)

                current_attrs['form'] = current_form
                current_attrs['token_id'] = '_'.join(current_token_ids)
                current_attrs['gloss'] = combined_tooltip
                current_attrs['tokens_info'] = tokens_info

                merged_words.append(current_attrs)

            sentences.append({
                'sent_id': sent_id,
                'text': text_val,
                'translated_text': translated_text,
                'words': merged_words,                 # for display
                'original_words': list(word_map.values()),  # for BRAT/relations
                'relations': relations
            })

        return sentences

    # -------------------------------------------------------------------
    # Utility: counts derived from sentence payload (if needed in UI)
    # -------------------------------------------------------------------
    def _counts_from_sentences(sentences):
        """
        Derive:
          - token_hits: total selected verb tokens across all sentences
          - sentence_hits: number of sentences containing at least one selected verb token
        """
        if not sentences:
            return 0, 0

        sentence_hits = 0
        token_hits = 0
        for s in sentences:
            has_selected = False
            for w in s.get('original_words', []):
                if w.get('is_selected_verb'):
                    token_hits += 1
                    has_selected = True
            if has_selected:
                sentence_hits += 1

        return token_hits, sentence_hits

    # -------------------------------------------------------------------
    # Selected-verb paging + payload retrieval
    # -------------------------------------------------------------------
    page_occurrence_start = page_occurrence_end = 0
    selected_verb_token_count = 0
    selected_verb_sentence_count = 0

    if selected_verb:
        (selected_verb_sentence_count,
         selected_verb_token_count,
         prev_tokens_cum,
         page_sent_ids,
         page_token_total) = get_selected_verb_totals_and_page_ids(
            selected_verb, selected_verb_gloss, dependencies, selected_sources,
            page=page, per_page=per_page, offset=offset
        )

        # Display range (occurrence indices) shown in the UI, e.g. "51–100 occurrences".
        if page_token_total > 0:
            page_occurrence_start = prev_tokens_cum + 1
            page_occurrence_end = prev_tokens_cum + page_token_total

        # Scope sentence fetching to current page sentence IDs.
        def get_sentences_by_ids(sent_ids):
            if not sent_ids:
                return []
            return get_sentences_scoped(selected_verb, selected_verb_gloss, dependencies, sent_ids, selected_sources)

        def get_sentences_scoped(selected_verb, selected_verb_gloss, dependencies, page_sent_ids, selected_sources=None):
            """
            Same as get_sentences(), but restricted to a provided list of sent_ids.

            Customization:
            - On the transliteration page you select s.transliterated_text instead of s.text.
              This is schema-specific.
            """
            if not selected_verb:
                return []
            if selected_sources is None:
                selected_sources = []

            conditions = ["v.lemma = :selected_verb"]
            params = {'selected_verb': selected_verb}
            joins = ''

            # Scope by sent_ids for the current page.
            conditions.append("s.sent_id IN :page_ids")
            params["page_ids"] = tuple(page_sent_ids if page_sent_ids else [-1])

            source_filter = build_sources_condition(selected_sources, alias="s.sent_id")
            if source_filter:
                conditions.append(source_filter)

            if selected_verb_gloss:
                conditions.append("v.gloss = :selected_verb_gloss")
                params['selected_verb_gloss'] = selected_verb_gloss

            active_dependencies = []
            for idx, dep in enumerate(dependencies):
                if dep.get('deprel') or dep.get('case_value') or dep.get('lemma'):
                    dep = {**dep, 'idx': idx}
                    active_dependencies.append(dep)
                    alias = f'a{idx}'
                    joins += (
                        f"LEFT JOIN arguments {alias} "
                        f"ON v.token_id = {alias}.head_id AND v.sent_id = {alias}.sent_id\n"
                    )
                    if dep.get('deprel'):
                        conditions.append(f"{alias}.dep_rel = :deprel{idx}")
                        params[f'deprel{idx}'] = dep['deprel']
                    if dep.get('case_value'):
                        conditions.append(f"{alias}.case_value = :case_value{idx}")
                        params[f'case_value{idx}'] = dep['case_value']
                    if dep.get('lemma'):
                        conditions.append(f"{alias}.lemma = :lemma{idx}")
                        params[f'lemma{idx}'] = dep['lemma']

            if len(active_dependencies) >= 2:
                for i in range(len(active_dependencies)):
                    for j in range(i + 1, len(active_dependencies)):
                        idx_i = active_dependencies[i]['idx']
                        idx_j = active_dependencies[j]['idx']
                        conditions.append(f"a{idx_i}.token_id != a{idx_j}.token_id")

            # Feature filters (v.*)
            if selected_verbforms:
                conditions.append("v.VerbForm IN :verbforms_list"); params["verbforms_list"] = tuple(selected_verbforms)
            if selected_aspects:
                conditions.append("v.Aspect IN :aspects_list"); params["aspects_list"] = tuple(selected_aspects)
            if selected_cases:
                conditions.append("v.Case IN :cases_list"); params["cases_list"] = tuple(selected_cases)
            if selected_connegatives:
                conditions.append("v.Connegative IN :conneg_list"); params["conneg_list"] = tuple(selected_connegatives)
            if selected_moods:
                conditions.append("v.Mood IN :moods_list"); params["moods_list"] = tuple(selected_moods)
            if selected_numbers:
                conditions.append("v.Number IN :numbers_list"); params["numbers_list"] = tuple(selected_numbers)
            if selected_persons:
                conditions.append("v.Person IN :persons_list"); params["persons_list"] = tuple(selected_persons)
            if selected_tenses:
                conditions.append("v.Tense IN :tenses_list"); params["tenses_list"] = tuple(selected_tenses)
            if selected_voices:
                conditions.append("v.Voice IN :voices_list"); params["voices_list"] = tuple(selected_voices)

            where_clause = ' AND '.join(conditions)

            # Sentence headers:
            # Customization: here you are using transliterated_text in this scoped variant.
            sentences_basic_info = db.session.execute(text(f"""
                SELECT DISTINCT s.sent_id,
                       s.transliterated_text AS text,
                       s.translated_text
                FROM sentences s
                JOIN verbs v ON s.sent_id = v.sent_id
                {joins}
                WHERE {where_clause}
                GROUP BY s.sent_id, s.transliterated_text, s.translated_text
                ORDER BY s.sent_id
            """), params).fetchall()

            if not sentences_basic_info:
                return []

            # Selected verb tokens per sentence
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

            # Batch pull words + arguments
            sent_ids = [row[0] for row in sentences_basic_info]
            safe_sent_ids = tuple(sent_ids) if sent_ids else tuple([-1])

            words_all = db.session.execute(text("""
                SELECT w.sent_id, w.token_id, w.form, CAST(w.feat AS CHAR), w.gloss, w.head_id, w.dep_rel, w.pos
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

            # Build the final sentence objects (same assembly logic as get_sentences()).
            sentences = []

            for sent_id, text_val, translated_text in sentences_basic_info:
                words = words_by_sent.get(sent_id, [])
                if not words:
                    continue

                word_map = {}
                for w in words:
                    feat = w[3] if w[3] != 'None' else None
                    if feat:
                        feat_parts = list(set(feat.split('|')))
                        feat = '|'.join(feat_parts)

                    token_id_int = int(w[1])
                    head_id_int = int(w[5]) if w[5] is not None else None

                    word_map[token_id_int] = {
                        'token_id': token_id_int,
                        'form': w[2] if w[2] is not None else '',
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

                selected_token_ids = verb_token_ids_per_sent.get(sent_id, [])
                if not selected_token_ids:
                    continue

                relations = []
                relevant_args = []
                heads = set(selected_token_ids)
                for a in args_by_sent.get(sent_id, []):
                    if int(a.head_id) in heads:
                        relevant_args.append(a)

                for verb_token_id in selected_token_ids:
                    if verb_token_id in word_map:
                        word_map[verb_token_id]['is_selected_verb'] = True

                for arg in relevant_args:
                    head_id = int(arg.head_id)
                    arg_token_id = int(arg.token_id) if arg.token_id is not None else None
                    cdep_token_id = int(arg.cdep_token_id) if arg.cdep_token_id is not None else None
                    second_cdep_token_id = int(arg.second_cdep_token_id) if arg.second_cdep_token_id is not None else None
                    fdep_token_id = int(arg.fdep_token_id) if arg.fdep_token_id is not None else None
                    dep_rel = arg.dep_rel or 'argument'

                    if arg_token_id and arg_token_id in word_map:
                        word_map[arg_token_id]['is_argument'] = True
                        relations.append({'from': head_id, 'to': arg_token_id, 'dep_rel': dep_rel})

                    if cdep_token_id and cdep_token_id in word_map:
                        word_map[cdep_token_id]['is_case_dependent'] = True
                        relations.append({'from': arg_token_id if arg_token_id else head_id, 'to': cdep_token_id, 'dep_rel': 'case_dependency'})

                    if second_cdep_token_id and second_cdep_token_id in word_map:
                        word_map[second_cdep_token_id]['is_case_dependent'] = True
                        relations.append({'from': arg_token_id if arg_token_id else head_id, 'to': second_cdep_token_id, 'dep_rel': 'case_dependency'})

                    if fdep_token_id and fdep_token_id in word_map:
                        word_map[fdep_token_id]['is_fixed_dependent'] = True
                        if second_cdep_token_id:
                            relations.append({'from': second_cdep_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                        elif cdep_token_id:
                            relations.append({'from': cdep_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                        elif arg_token_id:
                            relations.append({'from': arg_token_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})
                        else:
                            relations.append({'from': head_id, 'to': fdep_token_id, 'dep_rel': 'fixed_dependency'})

                # Display merging 
                original_words = list(word_map.values())
                merged_words = []

                tokens_attach_to_next = {'յ', 'զ', 'ց', 'չ', 'Յ', 'Զ', 'Ց', 'Չ'}
                tokens_attach_to_prev = {'ս', 'դ', 'ն', '՝', '.', ':', ','}
                special_attach_tokens = {'՞', '՛'}
                vowels = {'ա', 'ե', 'է', 'ը', 'ի', 'օ', 'ու', 'ո', 'Է'}

                i = 0
                while i < len(original_words):
                    current_token = original_words[i]
                    current_form = current_token['form']
                    current_token_ids = [str(current_token['token_id'])]
                    current_attrs = current_token.copy()
                    current_attrs['token_id'] = '_'.join(current_token_ids)
                    tokens_info = current_token.get('tokens_info', [{'gloss': current_token.get('gloss'), 'feat': current_token.get('feat')}])
                    i += 1

                    if current_form in special_attach_tokens and merged_words:
                        last_word = merged_words.pop()
                        last_form = last_word['form']
                        vowel_indices = [idx for idx, char in enumerate(last_form) if char in vowels]
                        insert_pos = (vowel_indices[-1] + 1) if vowel_indices else len(last_form)
                        last_word['form'] = last_form[:insert_pos] + current_form + last_form[insert_pos:]
                        last_word['tokens_info'] += tokens_info

                        last_word['is_selected_verb'] = last_word['is_selected_verb'] or current_attrs['is_selected_verb']
                        last_word['is_argument'] = last_word['is_argument'] or current_attrs['is_argument']
                        last_word['is_case_dependent'] = last_word['is_case_dependent'] or current_attrs['is_case_dependent']
                        last_word['is_fixed_dependent'] = last_word['is_fixed_dependent'] or current_attrs['is_fixed_dependent']
                        last_word['token_id'] = str(last_word['token_id']) + '_' + str(current_attrs['token_id'])

                        tooltip_parts = []
                        for token_info in last_word['tokens_info']:
                            gloss = token_info.get('gloss')
                            feat = token_info.get('feat')
                            if gloss and feat:
                                tooltip_part = f"{gloss}.{feat}"
                            elif gloss:
                                tooltip_part = gloss
                            elif feat:
                                tooltip_part = feat
                            else:
                                tooltip_part = ''
                            if tooltip_part:
                                tooltip_parts.append(tooltip_part)

                        last_word['gloss'] = '='.join(tooltip_parts)
                        merged_words.append(last_word)
                        continue

                    while current_form in tokens_attach_to_prev and merged_words:
                        last_word = merged_words.pop()
                        current_form = last_word['form'] + current_form
                        current_token_ids = last_word['token_id'].split('_') + current_token_ids
                        tokens_info = last_word['tokens_info'] + tokens_info
                        current_attrs['is_selected_verb'] = current_attrs['is_selected_verb'] or last_word['is_selected_verb']
                        current_attrs['is_argument'] = current_attrs['is_argument'] or last_word['is_argument']
                        current_attrs['is_case_dependent'] = current_attrs['is_case_dependent'] or last_word['is_case_dependent']
                        current_attrs['is_fixed_dependent'] = current_attrs['is_fixed_dependent'] or last_word['is_fixed_dependent']
                        current_attrs['pos'] = current_attrs['pos'] or last_word['pos']

                    while current_form in tokens_attach_to_next and i < len(original_words):
                        next_token = original_words[i]
                        current_form += next_token['form']
                        current_token_ids.append(str(next_token['token_id']))
                        tokens_info += next_token.get('tokens_info', [{'gloss': next_token.get('gloss'), 'feat': next_token.get('feat')}])
                        current_attrs['is_selected_verb'] = current_attrs['is_selected_verb'] or next_token['is_selected_verb']
                        current_attrs['is_argument'] = current_attrs['is_argument'] or next_token['is_argument']
                        current_attrs['is_case_dependent'] = current_attrs['is_case_dependent'] or next_token['is_case_dependent']
                        current_attrs['is_fixed_dependent'] = current_attrs['is_fixed_dependent'] or next_token['is_fixed_dependent']
                        current_attrs['pos'] = current_attrs['pos'] or next_token['pos']
                        i += 1
                        if next_token['form'] not in tokens_attach_to_next:
                            break

                    tooltip_parts = []
                    for token_info in tokens_info:
                        gloss = token_info.get('gloss')
                        feat = token_info.get('feat')
                        if gloss and feat:
                            tooltip_part = f"{gloss}.{feat}"
                        elif gloss:
                            tooltip_part = gloss
                        elif feat:
                            tooltip_part = feat
                        else:
                            tooltip_part = ''
                        tooltip_parts.append(tooltip_part)
                    tooltip_parts = [part for part in tooltip_parts if part]
                    combined_tooltip = '='.join(tooltip_parts)

                    current_attrs['form'] = current_form
                    current_attrs['token_id'] = '_'.join(current_token_ids)
                    current_attrs['gloss'] = combined_tooltip
                    current_attrs['tokens_info'] = tokens_info
                    merged_words.append(current_attrs)

                sentences.append({
                    'sent_id': sent_id,
                    'text': text_val,
                    'translated_text': translated_text,
                    'words': merged_words,
                    'original_words': list(word_map.values()),
                    'relations': relations
                })

            return sentences

        sentences = get_sentences_by_ids(page_sent_ids)

        # When a verb is selected, the "total_sentence_count" shown should reflect the selected-verb sentence set.
        total_sentence_count = selected_verb_sentence_count

    else:
        # No selected verb: verbs list mode has no sentence payload by default.
        sentences = []
        total_sentence_count = get_total_sentence_count(
            dependencies, initial_letter, language_search_query, english_search_query,
            selected_verb=None, selected_verb_gloss=None, selected_sources=selected_sources
        )

    # -------------------------------------------------------------------
    # BRAT export generator for each sentence
    # -------------------------------------------------------------------
    def generate_brat_data(sentences):
        """
        Attach BRAT-compatible annotation payload for each sentence.

        Customization:
        - Entity typing currently uses POS (and SelectedVerb_{POS} for selected verbs).
        - Attribute extraction currently extracts only Case from feats.
        """
        if not sentences:
            return None

        for sentence in sentences:
            words = sentence['original_words']  # use unmerged tokens for stable offsets
            relations = sentence.get('relations', [])

            # Build plain text with spaces and compute per-token character offsets.
            text = ''
            offsets = []
            for word in words:
                word_form = word['form']
                start_offset = len(text)
                text += word_form + ' '
                end_offset = len(text) - 1
                offsets.append((start_offset, end_offset))

            text = text.strip()

            entities = []
            attributes = []
            brat_relations = []

            token_id_to_entity_id = {}
            for idx, (word, (start, end)) in enumerate(zip(words, offsets)):
                entity_id = f"T{idx + 1}"
                token_id_to_entity_id[int(word['token_id'])] = entity_id

                if word.get('is_selected_verb'):
                    entity_type = f"SelectedVerb_{word.get('pos', 'Token')}"
                else:
                    entity_type = word.get('pos', 'Token')

                entities.append([entity_id, entity_type, [[start, end]]])

                # Extract a Case attribute if present
                if word.get('feat'):
                    feat_parts = word['feat'].split('|')
                    for part in feat_parts:
                        if part.startswith('Case='):
                            case_value = part.split('=')[1]
                            attr_id = f"A{idx + 1}"
                            attributes.append([attr_id, 'Case', entity_id, case_value])
                            break

            for idx, rel in enumerate(relations):
                from_token_id = int(rel['from'])
                to_token_id = int(rel['to'])
                dep_rel = rel['dep_rel']

                from_entity = token_id_to_entity_id.get(from_token_id)
                to_entity = token_id_to_entity_id.get(to_token_id)

                if from_entity and to_entity:
                    relation_id = f"R{idx + 1}"
                    brat_relations.append([relation_id, dep_rel, [['Governor', from_entity], ['Dependent', to_entity]]])

            sentence['brat_data'] = {
                'text': text,
                'entities': entities,
                'attributes': attributes,
                'relations': brat_relations
            }

    brat_data = generate_brat_data(sentences)

    # -------------------------------------------------------------------
    # Feature dropdown values for UI (JSON-friendly)
    # -------------------------------------------------------------------
    raw_feature_values = get_dynamic_feature_values(
        dependencies=dependencies,
        selected_sources=selected_sources
    )
    server_feature_values = {feature: sorted(list(values)) for feature, values in raw_feature_values.items()}

    # -------------------------------------------------------------------
    # Initials bar generation under current filters
    # -------------------------------------------------------------------
    initials_for_bar = get_initials_under_filters(
        dependencies=dependencies,
        selected_sources=selected_sources,
        initial_letter=None if selected_verb else initial_letter,
        include_selected_verb=False,
        include_search_filters=False if selected_verb else True
    )

    initial_links = []
    base_args = MultiDict(request.args)

    # On sentences page, initials should behave as "go back to verbs list mode".
    if selected_verb:
        for k in ('selected_verb', 'selected_verb_gloss', 'language_search_query', 'english_search_query'):
            try:
                base_args.pop(k)
            except KeyError:
                pass

    for letter in initials_for_bar:
        args_copy = MultiDict(base_args)
        args_copy.setlist('initial', [letter])
        if selected_verb:
            # clear sticky sense when jumping via initials
            args_copy.setlist('selected_verb', [''])
            args_copy.setlist('selected_verb_gloss', [''])
        url = url_for('home') + '?' + urlencode(list(args_copy.lists()), doseq=True)
        initial_links.append({'letter': letter, 'url': url})

    clear_args = MultiDict(base_args)
    clear_args.setlist('initial', [''])
    if selected_verb:
        clear_args.setlist('selected_verb', [''])
        clear_args.setlist('selected_verb_gloss', [''])
    clear_initials_url = url_for('home') + '?' + urlencode(list(clear_args.lists()), doseq=True)

    # -------------------------------------------------------------------
    # User feature selections (for UI display)
    # -------------------------------------------------------------------
    user_feature_selections = {}
    if selected_verbforms: user_feature_selections['VerbForm'] = selected_verbforms
    if selected_aspects: user_feature_selections['Aspect'] = selected_aspects
    if selected_cases: user_feature_selections['Case'] = selected_cases
    if selected_connegatives: user_feature_selections['Negation'] = selected_connegatives
    if selected_moods: user_feature_selections['Mood'] = selected_moods
    if selected_numbers: user_feature_selections['Number'] = selected_numbers
    if selected_persons: user_feature_selections['Person'] = selected_persons
    if selected_tenses: user_feature_selections['Tense'] = selected_tenses
    if selected_voices: user_feature_selections['Voice'] = selected_voices

    # -------------------------------------------------------------------
    # Customizable: language <-> transliteration conversion for URL switching
    # -------------------------------------------------------------------
    def language_to_translit_text(s: str) -> str:
        """
        Transliterate the *script characters* using initial_map_language_to_translit.

        Customization:
        - Replace mapping if you support a different script or transliteration standard.
        """
        return ''.join(initial_map_language_to_translit.get(ch, ch) for ch in s)

    def normalize_case_value_for_translit(val: str) -> str:
        """
        Normalize case_value strings that are formatted like: "Acc + X".
        Only transliterate the right-hand side; keep the left label unchanged.

        Customization:
        - If your case_value encoding uses a different separator, update this parser.
        """
        if '+' in val:
            left, right = val.split('+', 1)
            return f"{left.strip()} + {language_to_translit_text(right.strip())}"
        return language_to_translit_text(val)

    # -------------------------------------------------------------------
    # Build switch_url: language page -> translit page (preserve filters safely)
    # -------------------------------------------------------------------
    base_query = request.query_string.decode('utf-8')
    qs = parse_qs(base_query, keep_blank_values=True)

    # Ensure sense/gloss survives the switch.
    if selected_verb_gloss:
        qs['selected_verb_gloss'] = [selected_verb_gloss]

    # Preserve source selection state.
    if qs.get('selected_source'):
        qs['source_checkbox_submitted'] = ['1']

    # Rename language-side parameters to transliteration-side equivalents.
    if 'language_search_query' in qs:
        qs['translit_search_query'] = qs.pop('language_search_query')
    if 'case_dependant_lemma' in qs:
        qs['translit_lemma'] = qs.pop('case_dependant_lemma')

    # Preserve the effective initial (session-backed), mapping it to translit initial if possible.
    effective_initial_arm = initial_letter or ''
    if effective_initial_arm:
        mapped = initial_map_language_to_translit.get(effective_initial_arm, '')
        if mapped:
            qs['initial'] = [mapped]
        else:
            qs.pop('initial', None)
    else:
        qs.pop('initial', None)
        qs.pop('reset', None)

    # Keys involved in dependency filters (lemma + encoding/case_value)
    lemma_keys = ['translit_lemma'] + [f'co_occurring_lemma_{i}' for i in range(2, 6)]
    enc_keys = ['case_value'] + [f'co_occurring_case_value_{i}' for i in range(2, 6)]

    # 1) Convert argument lemmas: language -> transliteration (context-aware)
    arm_lemmas = [qs[k][0] for k in lemma_keys if k in qs and qs[k] and qs[k][0]]
    lemma_map = _fetch_translit_for_arg_lemmas(arm_lemmas, vlemma=selected_verb, vgloss=selected_verb_gloss)

    for k in lemma_keys:
        if k in qs and qs[k] and qs[k][0]:
            aval = qs[k][0]
            tval = lemma_map.get(aval)
            if tval:
                qs[k] = [tval]
            else:
                # Drop unknown mappings to avoid “over-filtering” after switching pages.
                qs.pop(k, None)

    # 2) Convert full case_value to translit dep token (“dep bit”), based on context.
    orig_qs = parse_qs(base_query, keep_blank_values=True)

    def _ctx_for_key_arm_to_tr(kname: str):
        """
        Return (dep_rel_ctx, tlemma_ctx) for the dependency row associated with param key.

        NOTE:
        - tlemma_ctx is already in transliteration in qs at this point, because we rewrote lemma keys above.
        """
        if kname == 'case_value':
            dep_rel = qs.get('syntactic_relation', [''])[0] or None
            tlemma = qs.get('translit_lemma', [''])[0] or None
            return dep_rel, tlemma

        m = re.match(r'co_occurring_case_value_(\d+)$', kname)
        if m:
            i = int(m.group(1))
            dep_rel = qs.get(f'co_occurring_deprel_{i}', [''])[0] or None
            tlemma = qs.get(f'co_occurring_lemma_{i}', [''])[0] or None
            return dep_rel, tlemma

        return None, None

    present_enc = {}
    for k in enc_keys:
        raw_cv = orig_qs.get(k, [''])[0].strip()
        if raw_cv:
            present_enc[k] = raw_cv

    # Clear enc keys first to avoid passing stale "Acc + ..." strings to /translit.
    for k in enc_keys:
        qs.pop(k, None)

    for k, raw_cv in present_enc.items():
        dep_rel_ctx, tlemma_ctx = _ctx_for_key_arm_to_tr(k)
        cv_map = _fetch_tbits_from_full_case_values(
            [raw_cv],
            dep_rel=dep_rel_ctx,
            tlemma=tlemma_ctx,
            vlemma=selected_verb,
            vgloss=selected_verb_gloss
        )
        cands = sorted(list(cv_map.get(raw_cv, set())))
        if len(cands) == 1:
            qs[k] = [cands[0]]
        else:
            # Ambiguous mapping: drop to prevent incorrect filters / oscillation.
            pass

    # Selected verb: language lemma -> translit_verb, disambiguated by gloss if possible.
    if 'selected_verb' in qs and qs['selected_verb']:
        arm_lemma = qs['selected_verb'][0]
        gloss_ctx = selected_verb_gloss or (qs.get('selected_verb_gloss', [None])[0] or None)

        if gloss_ctx:
            row = db.session.execute(
                text("""
                    SELECT translit_verb
                    FROM verbs
                    WHERE lemma = :v AND gloss = :g
                    LIMIT 1
                """),
                {'v': arm_lemma, 'g': gloss_ctx}
            ).fetchone()
        else:
            row = db.session.execute(
                text("""
                    SELECT translit_verb
                    FROM verbs
                    WHERE lemma = :v
                    LIMIT 1
                """),
                {'v': arm_lemma}
            ).fetchone()

        if row and row.translit_verb:
            qs['selected_verb'] = [row.translit_verb]
            if gloss_ctx:
                qs['selected_verb_gloss'] = [gloss_ctx]
        else:
            qs.pop('selected_verb', None)
            qs.pop('selected_verb_gloss', None)
    else:
        qs.pop('selected_verb_gloss', None)

    # Reset paging after switching pages
    qs['page'] = ['1']

    switch_qs = urlencode(qs, doseq=True)
    switch_url = url_for('translit') + ('?' + switch_qs if switch_qs else '')

    # -------------------------------------------------------------------
    # Final template context
    # -------------------------------------------------------------------
    context = {
        'verbs_with_frequencies': verbs_with_frequencies,
        'sort_order': sort_order,
        'order_direction': order_direction,
        'search_query': search_query,
        'dependencies': dependencies,
        'sentences': sentences,
        'brat_data': brat_data,
        'selected_verb': selected_verb,
        'selected_verb_url': selected_verb_url,
        'selected_verb_gloss': selected_verb_gloss,
        'dependency_visible_flags': dependency_visible_flags,
        'total_sentence_count': total_sentence_count,
        'language_search_query': language_search_query,
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
        'switch_url': switch_url,
        'selected_verb_token_count': selected_verb_token_count,
        'selected_verb_sentence_count': selected_verb_sentence_count,
        'initial_letters': initials_for_bar,   # keep key name if your template expects it
        'initial_links': initial_links,
        'clear_initials_url': clear_initials_url,
        'initial_letter': initial_letter,
        'page': page,
        'per_page': per_page,
        'has_prev': page > 1,
        'has_next': (page * per_page) < total_sentence_count,
        'page_occurrence_start': page_occurrence_start,
        'page_occurrence_end': page_occurrence_end,
    }

    return render_template('home.html', enumerate=enumerate, **context)
