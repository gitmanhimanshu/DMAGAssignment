document.getElementById('generate-btn').addEventListener('click', async () => {
    const requestId = document.getElementById('request-id').value;
    const requestText = document.getElementById('request-text').value;
    
    const loading = document.getElementById('loading');
    const resultSection = document.getElementById('result-section');
    const errorMsg = document.getElementById('error');
    
    loading.classList.remove('hidden');
    resultSection.classList.add('hidden');
    errorMsg.classList.add('hidden');
    
    try {
        const response = await fetch('http://localhost:8000/plan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                request_id: requestId,
                text: requestText
            })
        });
        
        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }
        
        const data = await response.json();
        displayResult(data);
    } catch (error) {
        errorMsg.textContent = error.message;
        errorMsg.classList.remove('hidden');
    } finally {
        loading.classList.add('hidden');
    }
});

function displayResult(plan) {
    document.getElementById('plan-status').textContent = plan.status === 'success' ? 'Itinerary Generated' : 'Request Unfulfillable';
    document.getElementById('plan-summary').textContent = plan.summary;
    document.getElementById('plan-total').textContent = `Total: ₹${plan.total_price}`;
    document.getElementById('plan-validation').textContent = `Status: ${plan.grounding_status}`;
    document.getElementById('plan-review').textContent = plan.human_review_required ? 'Human Review Required' : '';
    
    const daysContainer = document.getElementById('days-container');
    daysContainer.innerHTML = '';
    
    if (plan.days) {
        plan.days.forEach(day => {
            const dayCard = document.createElement('div');
            dayCard.className = 'day-card';
            
            let itemsHtml = '';
            day.items.forEach(item => {
                itemsHtml += `
                    <div class="item-card">
                        <div class="item-header">
                            <span>${item.name} <span class="item-id">(${item.catalog_id})</span></span>
                            <span>₹${item.line_total}</span>
                        </div>
                        <div style="font-size: 0.9em; color: #555;">
                            ${item.item_type.toUpperCase()} | Qty: ${item.quantity} @ ₹${item.unit_price} | ${item.location}
                        </div>
                        <div style="font-size: 0.9em; margin-top: 5px;">
                            <i>${item.reason}</i>
                        </div>
                    </div>
                `;
            });
            
            dayCard.innerHTML = `
                <h3>Day ${day.day_number}: ${day.summary}</h3>
                ${itemsHtml}
            `;
            daysContainer.appendChild(dayCard);
        });
    }
    
    document.getElementById('result-section').classList.remove('hidden');
}
