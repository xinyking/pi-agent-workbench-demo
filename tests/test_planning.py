import unittest
from workbench.planning import validate_plan,prepare_batch

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[{'name':'researcher'},{'name':'writer'}]
        self.steps=[{'id':'research','title':'调查','task':'整理资料','agent':'researcher'}, {'id':'report','title':'报告','task':'撰写报告','agent':'writer','depends_on':['research']}]

    def test_dependencies_are_enforced_and_batch_validation_is_atomic(self):
        plan=validate_plan(self.steps,self.catalog)
        with self.assertRaisesRegex(ValueError,'依赖'):prepare_batch([{'step_id':'report','agent':'writer'}],plan)
        self.assertTrue(all(s['status']=='pending' for s in plan))
        prepare_batch([{'step_id':'research','agent':'researcher'}],plan)
        self.assertEqual(plan[0]['status'],'running')
        plan[0]['status']='completed'
        prepare_batch([{'step_id':'report','agent':'writer'}],plan)
        self.assertEqual(plan[1]['status'],'running')

    def test_invalid_graphs_and_roles_are_rejected(self):
        for steps in [[{**self.steps[0],'depends_on':['report']},self.steps[1]], [self.steps[0],{**self.steps[1],'depends_on':['missing']}], [self.steps[0],self.steps[0]], [{**self.steps[0],'agent':'disabled'}]]:
            with self.subTest(steps=steps),self.assertRaises(ValueError):validate_plan(steps,self.catalog)

    def test_duplicate_or_wrong_role_cannot_advance_a_step(self):
        plan=validate_plan(self.steps,self.catalog)
        with self.assertRaises(ValueError):prepare_batch([{'step_id':'research','agent':'writer'}],plan)
        self.assertEqual(plan[0]['status'],'pending')
        with self.assertRaises(ValueError):prepare_batch([{'step_id':'research','agent':'researcher'}]*2,plan)
        self.assertEqual(plan[0]['status'],'pending')

    def test_different_task_cannot_claim_the_planned_step(self):
        plan=validate_plan(self.steps,self.catalog)
        with self.assertRaisesRegex(ValueError,'任务与计划'):
            prepare_batch([{'step_id':'research','agent':'researcher','task':'执行其他无关工作'}],plan)
        self.assertEqual(plan[0]['status'],'pending')
