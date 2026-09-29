const PRESETS = {
    'REQ-1': {
        id: 'REQ-1',
        text: '5 days in Kerala for a family of 4 (2 adults, kids aged 8 and 11), mid-range budget around 60,000 total, we love nature and local food and prefer a relaxed pace.'
    },
    'REQ-2': {
        id: 'REQ-2',
        text: 'A couple wanting a 2-day weekend trip to Munnar, budget-conscious, focused on hiking and tea estates.'
    },
    'REQ-3': {
        id: 'REQ-3',
        text: '3 days in Goa for a solo traveler looking for beaches and nightlife.'
    }
};

function loadPreset(key) {
    const preset = PRESETS[key];
    if (preset) {
        document.getElementById('request-id').value = preset.id;
        document.getElementById('request-text').value = preset.text;
    }
}

document.getElementById('generate-btn').addEventListener('click', async () => {
    const requestId = document.getElementById('request-id').value.trim();
    const requestText = document.getElementById('request-text').value.trim();
    
    if (!requestText) {
        alert('Please enter a travel request.');
        return;
    }

    const loading = document.getElementById('loading');
    const resultSection = document.getElementById('result-section');
    const errorMsg = document.getElementById('error');
    
    loading.classList.remove('hidden');
    resultSection.classList.add('hidden');
    errorMsg.classList.add('hidden');
    
    // Support running either from same-origin FastAPI server or standalone file/browser
    const endpoint = window.location.origin.includes('localhost:8000') || window.location.origin.includes('127.0.0.1:8000')
        ? '/plan'
        : 'http://localhost:8000/plan';

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                request_id: requestId || 'REQ-CUSTOM',
                text: requestText
            })
        });
        
        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Server error: ${response.status} ${response.statusText}`);
        }
        
        const data = await response.json();
        displayResult(data);
    } catch (error) {
        errorMsg.innerHTML = `<strong>Error connecting to planner API:</strong> ${error.message}<br><small>Make sure the backend server is running via: <code>uvicorn src.api:app --reload</code></small>`;
        errorMsg.classList.remove('hidden');
    } finally {
        loading.classList.add('hidden');
    }
});

function displayResult(plan) {
    const isSuccess = plan.status === 'success';
    const summaryCard = document.getElementById('summary-card');
    const unfulfillableCard = document.getElementById('unfulfillable-card');
    const daysContainer = document.getElementById('days-container');
    
    // Status & Summary
    document.getElementById('plan-status').textContent = isSuccess ? 'Itinerary Generated & Priced' : 'Request Unfulfillable';
    document.getElementById('plan-status').style.color = isSuccess ? '#2e7d32' : '#c62828';
    document.getElementById('plan-summary').textContent = plan.summary;
    
    // Meta tags
    document.getElementById('plan-total').textContent = `Total Quote: ₹${(plan.total_price || 0).toLocaleString('en-IN')}`;
    document.getElementById('plan-validation').textContent = `Grounding: ${plan.grounding_status.toUpperCase()}`;
    document.getElementById('plan-review').textContent = plan.human_review_required ? 'Human Review Required' : 'Auto-Confirmed';
    
    // Human Review Reasons
    const reviewBox = document.getElementById('review-reasons-box');
    const reviewList = document.getElementById('review-reasons-list');
    reviewList.innerHTML = '';
    
    if (plan.human_review_required && plan.human_review_reasons && plan.human_review_reasons.length > 0) {
        plan.human_review_reasons.forEach(reason => {
            const li = document.createElement('li');
            li.textContent = reason;
            reviewList.appendChild(li);
        });
        reviewBox.classList.remove('hidden');
    } else {
        reviewBox.classList.add('hidden');
    }
    
    daysContainer.innerHTML = '';
    
    if (isSuccess && plan.days && plan.days.length > 0) {
        unfulfillableCard.classList.add('hidden');
        
        plan.days.forEach(day => {
            const dayCard = document.createElement('div');
            dayCard.className = 'day-card';
            
            // Calculate Day Total (strictly per t.txt: "Day total")
            const dayTotal = (day.items || []).reduce((sum, item) => sum + (item.line_total || 0), 0);
            
            let itemsHtml = '';
            if (!day.items || day.items.length === 0) {
                itemsHtml = `
                    <div class="empty-day-notice">
                        <em>Dedicated unstructured recovery day (intentional leisure / flexible family time — ₹0).</em>
                    </div>
                `;
            } else {
                day.items.forEach(item => {
                    itemsHtml += `
                        <div class="item-card">
                            <div class="item-header">
                                <span class="item-title">
                                    ${item.name}
                                    <span class="item-id">[${item.catalog_id}]</span>
                                </span>
                                <span class="item-total">₹${(item.line_total || 0).toLocaleString('en-IN')}</span>
                            </div>
                            <div class="item-meta">
                                <span class="badge ${item.item_type}">${item.item_type.toUpperCase()}</span>
                                <span>Quantity: <strong>${item.quantity}</strong></span>
                                <span>Price: <strong>₹${item.unit_price}</strong></span>
                                <span>Location: <strong>${item.location}</strong></span>
                            </div>
                            <div class="item-reason">
                                <strong>Why selected:</strong> ${item.reason}
                            </div>
                        </div>
                    `;
                });
            }
            
            dayCard.innerHTML = `
                <div class="day-header">
                    <h3>Day ${day.day_number}: ${day.summary}</h3>
                    <span class="day-total-badge">Day Total: ₹${dayTotal.toLocaleString('en-IN')}</span>
                </div>
                ${itemsHtml}
            `;
            daysContainer.appendChild(dayCard);
        });
    } else {
        // Unfulfillable State (REQ-3: Goa trap)
        unfulfillableCard.classList.remove('hidden');
        document.getElementById('unfulfillable-text').textContent = plan.summary || 'No matching catalog inventory found for this destination.';
    }
    
    document.getElementById('result-section').classList.remove('hidden');
}
