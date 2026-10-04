"""Static feedback form; submitting downloads JSON and never controls the GUI."""
import html
from diagagent.diagnosis.responsibility import RESPONSIBILITY
from diagagent.feedback.store import diagnosis_hash


def feedback_form(root):
    if not (root / "diagnosis.json").exists():
        return ""
    options = ''.join(f'<option>{html.escape(kind)}</option>' for kind in RESPONSIBILITY)
    return f'''<section><h2>Human Correction / 人工纠正</h2>
<form id="feedback" data-run="{html.escape(root.name, quote=True)}" data-hash="{diagnosis_hash(root)}">
<p><label>Author / 反馈人 <input name="author" required maxlength="200"></label></p>
<p><label>AI diagnosis <select name="verdict"><option value="correct">Diagnosis correct</option><option value="wrong">Diagnosis wrong</option></select></label></p>
<p><label>Corrected failure type <select name="kind"><option value="">Choose if diagnosis is wrong</option>{options}</select></label></p>
<p><label>Corrected step (optional) <input name="step" type="number" min="0"></label></p>
<p><label>Repair Instruction / 修复指令<br><textarea name="instruction" required rows="5" cols="70" maxlength="10000" placeholder="Resize to 512x512, or provide a JSON replacement action."></textarea></label></p>
<button type="submit">Download feedback JSON</button><p id="feedback-status" role="status"></p>
</form><p>Import the downloaded file with <code>diagagent feedback RUN --file FILE</code>, then create a repair plan. Execution requires an explicit repair-run command.</p></section>
<script src="feedback_form.js" defer></script>'''


FORM_SCRIPT = """
document.getElementById('feedback').addEventListener('submit', function(event) {
 event.preventDefault(); const fields = this.elements; const correct = fields.verdict.value === 'correct';
 const status = document.getElementById('feedback-status');
 if (!correct && !fields.kind.value) { status.textContent = 'Select a corrected failure type.'; return; }
 const record = {run_id: this.dataset.run, diagnosis_sha256: this.dataset.hash,
 diagnosis_correct: correct, corrected_failure_type: correct ? null : fields.kind.value,
 corrected_step: correct || fields.step.value === '' ? null : Number(fields.step.value),
 repair_instruction: fields.instruction.value, author: fields.author.value, source: 'human'};
 const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type: 'application/json'}));
 const link = document.createElement('a'); link.href = url; link.download = 'human_feedback.json'; link.click();
 setTimeout(() => URL.revokeObjectURL(url), 1000); status.textContent = 'Downloaded. Import this file to save your correction.';
});
"""
