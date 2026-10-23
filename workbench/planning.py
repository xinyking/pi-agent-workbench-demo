"""Validate declared plans; actual delegation alone advances execution status."""
def validate_plan(steps, catalog):
    if not isinstance(steps,list) or not 1<=len(steps)<=16:raise ValueError('计划需要 1–16 个步骤')
    names={a['name'] for a in catalog};result=[];ids=set()
    for step in steps:
        if not isinstance(step,dict):raise ValueError('步骤必须是对象')
        item={}
        for key,limit in [('id',48),('title',200),('task',8000)]:
            value=step.get(key)
            if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('无效步骤字段：'+key)
            item[key]=value.strip()
        if item['id'] in ids:raise ValueError('步骤 ID 不能重复')
        ids.add(item['id'])
        if step.get('agent') not in names:raise ValueError('步骤使用了未启用的 Agent')
        dependencies=step.get('depends_on',[])
        if not isinstance(dependencies,list) or any(not isinstance(d,str) for d in dependencies):raise ValueError('depends_on 需要步骤 ID 数组')
        item.update(agent=step['agent'],depends_on=list(dict.fromkeys(dependencies)),status='pending')
        result.append(item)
    visited=set();remaining={s['id']:set(s['depends_on']) for s in result}
    if any(not deps<=ids for deps in remaining.values()):raise ValueError('依赖步骤不存在')
    while remaining:
        ready=[key for key,deps in remaining.items() if deps<=visited]
        if not ready:raise ValueError('计划依赖不能成环')
        for key in ready:visited.add(key);del remaining[key]
    return result


def prepare_batch(tasks, plan):
    if not plan:return
    lookup={s['id']:s for s in plan};seen=set()
    for task in tasks:
        step=lookup.get(task.get('step_id'))
        if not step:raise ValueError('存在计划时，每项委派必须携带有效 step_id')
        if step['id'] in seen or step['status'] in ('running','completed'):raise ValueError('步骤已经执行或重复委派')
        seen.add(step['id'])
        if task.get('task') is not None and task['task']!=step['task']:raise ValueError('委派任务与计划步骤不一致，请使用计划中的 task 原文')
        if task['agent']!=step['agent']:raise ValueError('委派角色与计划步骤不一致')
        if any(lookup[d]['status']!='completed' for d in step['depends_on']):raise ValueError('依赖步骤尚未完成')
    for task in tasks:lookup[task['step_id']]['status']='running'
