async function fetchJSON(url, options={}) {
  const res = await fetch(url, Object.assign({headers: {"Content-Type": "application/json"}}, options));
  if (!res.ok) {
    let txt = await res.text();
    throw new Error(txt || res.statusText);
  }
  return res.json();
}

function money(n){ return new Intl.NumberFormat('es-CO', {style:'currency', currency:'COP'}).format(Number(n||0)); }

// ------ Carga inicial ------
document.addEventListener("DOMContentLoaded", async () => {
  await refreshProducts();
  await refreshAlerts();

  document.getElementById('btnAddProduct').addEventListener('click', createProduct);
  document.getElementById('btnAdjust').addEventListener('click', adjustInventory);

  document.getElementById('btnAddPurchaseItem').addEventListener('click', () => addPurchaseRow());
  document.getElementById('btnSavePurchase').addEventListener('click', savePurchase);

  document.getElementById('btnAddRemissionItem').addEventListener('click', () => addSaleRow('remissionItems'));
  document.getElementById('btnSaveRemission').addEventListener('click', saveRemission);

  document.getElementById('btnAddInvoiceItem').addEventListener('click', () => addSaleRow('invoiceItems'));
  document.getElementById('btnSaveInvoice').addEventListener('click', saveInvoice);
});

async function refreshAlerts(){
  // Low stock
  let low = await fetchJSON('/api/alerts/low-stock');
  let ul = document.getElementById('lowStockList');
  ul.innerHTML = '';
  if (low.length === 0){
    ul.innerHTML = '<li class="list-group-item">Sin alertas por ahora.</li>';
  } else {
    low.forEach(p => {
      let li = document.createElement('li');
      li.className = 'list-group-item d-flex justify-content-between align-items-center low';
      li.innerHTML = `<span>${p.sku} — <strong>${p.name}</strong></span><span>Stock: ${p.current_stock}</span>`;
      ul.appendChild(li);
    });
  }

  // Maintenance
  let maint = await fetchJSON('/api/alerts/maintenance');
  let ml = document.getElementById('maintenanceList');
  ml.innerHTML = '';
  if (maint.length === 0){
    ml.innerHTML = '<li class="list-group-item">Nada pendiente en las pr&oacute;ximas 2 semanas.</li>';
  } else {
    maint.forEach(m => {
      let li = document.createElement('li');
      const dueDate = m.due_date ? new Date(m.due_date + "T00:00:00") : null;
      const dueLabel = dueDate ? dueDate.toLocaleDateString() : 'Sin fecha';
      li.className = 'list-group-item d-flex justify-content-between align-items-center gap-3';
      const customer = m.customer || {};
      const contactParts = [customer.phone, customer.address].filter(Boolean).join(' · ');
      li.innerHTML = `
        <div class="maintenance-alert-info">
          <span class="fw-semibold">${customer.name || 'Cliente sin nombre'}</span>
          ${contactParts ? `<small class="text-muted">${contactParts}</small>` : ''}
          ${m.notes ? `<small class="text-muted">${m.notes}</small>` : ''}
        </div>
        <div class="d-flex align-items-center gap-2 flex-shrink-0">
          <span class="badge bg-light text-dark border border-primary-subtle">Vence: ${dueLabel}</span>
          <button type="button" class="btn btn-sm btn-success maintenance-check-btn" title="Marcar mantenimiento realizado" onclick="completeMaintenance(${m.id}, this)">
            <i class="fas fa-check"></i>
          </button>
        </div>`;
      ml.appendChild(li);
    });
  }
}

async function completeMaintenance(reminderId, btn){
  if (!confirm('Confirma que este cliente ya realizo el mantenimiento?')) {
    return;
  }
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
  try {
    let res = await fetch(`/api/alerts/maintenance/complete?id=${reminderId}`);

    if (!res.ok) {
      res = await fetch('/api/alerts/maintenance/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: reminderId })
      });
    }

    if (!res.ok && (res.status === 404 || res.status === 405)) {
      res = await fetch(`/api/alerts/maintenance/${reminderId}`, { method: 'DELETE' });
    }

    if (!res.ok && (res.status === 404 || res.status === 405)) {
      res = await fetch(`/api/alerts/maintenance/${reminderId}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }

    await refreshAlerts();
  } catch (error) {
    btn.disabled = false;
    btn.innerHTML = originalHTML;
    alert('No se pudo marcar el mantenimiento como realizado: ' + error.message);
  }
}

async function refreshProducts(){
  let products = await fetchJSON('/api/products');
  const selectAdjust = document.getElementById('adjustProduct');
  const tableBody = document.querySelector('#productTable tbody');
  selectAdjust.innerHTML = '';
  tableBody.innerHTML = '';

  products.forEach(p => {
    let opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `${p.sku} — ${p.name}`;
    selectAdjust.appendChild(opt);

    let tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${p.sku}</td>
      <td>${p.name}</td>
      <td>${money(p.price)}</td>
      <td>${money(p.vat_amount || 0)}</td>
      <td>${money(p.price_with_vat || 0)}</td>
      <td>${p.supplier_name || 'Sin proveedor'}</td>
      <td>${p.total_sold || 0}</td>
      <td>${p.current_stock}</td>
      <td>${p.low_stock_threshold}</td>
      <td style="text-align:center">
        <button class="btn btn-sm btn-link text-danger" title="Eliminar" onclick="deleteProduct(${p.id}, this)">
          <svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' fill='currentColor' viewBox='0 0 16 16'><path d='M5.5 5.5a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0v-6a.5.5 0 0 1 .5-.5zm2.5.5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0v-6zm3 .5a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0v-6a.5.5 0 0 1 .5-.5z'/><path fill-rule='evenodd' d='M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1H13a1 1 0 0 1 1 1v1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z'/></svg>
        </button>
      </td>
    `;
    tableBody.appendChild(tr);
  });
}

async function deleteProduct(id, btn){
  if (!confirm('¿Seguro que deseas eliminar este producto?')) return;
  btn.disabled = true;
  try {
    let res = await fetch(`/api/products/${id}`, {method:'DELETE'});
    if (!res.ok) {
      let data = await res.json();
      alert('No se pudo eliminar: ' + (data.error || 'Error desconocido'));
      btn.disabled = false;
      return;
    }
    let data = await res.json();
    alert(data.message || 'Producto eliminado correctamente');
    await refreshProducts();
    await refreshAlerts();
  } catch(e){
    alert('Error eliminando producto: ' + e.message);
    btn.disabled = false;
  }
}

async function createProduct(){
  let body = {
    name: document.getElementById('prodName').value.trim(),
    sku: document.getElementById('prodSKU').value.trim(),
    price: parseFloat(document.getElementById('prodPrice').value || '0'),
    vat_rate: 0.19,
    low_stock_threshold: parseInt(document.getElementById('prodLow').value || '5')
  };
  try {
    await fetchJSON('/api/products', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('prodName').value = '';
    document.getElementById('prodSKU').value = '';
    document.getElementById('prodPrice').value = '';
    document.getElementById('prodLow').value = '5';
    await refreshProducts();
    await refreshAlerts();
  } catch (e){
    alert("Error creando producto: " + e.message);
  }
}

async function adjustInventory(){
  let body = {
    product_id: parseInt(document.getElementById('adjustProduct').value),
    quantity: parseInt(document.getElementById('adjustQty').value || '0'),
    reason: document.getElementById('adjustReason').value || 'ajuste'
  };
  if (!body.product_id || !body.quantity){ alert("Selecciona producto y cantidad"); return; }
  try {
    await fetchJSON('/api/inventory/adjust', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('adjustQty').value = '1';
    document.getElementById('adjustReason').value = '';
    await refreshProducts();
    await refreshAlerts();
  } catch (e){
    alert("Error en ajuste: " + e.message);
  }
}

// --- Compras ---
let purchaseRowIdx = 0;
function addPurchaseRow(){
  purchaseRowIdx++;
  const tbody = document.querySelector('#purchaseItems tbody');
  const tr = document.createElement('tr');
  tr.dataset.idx = purchaseRowIdx;
  tr.innerHTML = `
    <td>
      <div class="position-relative">
        <select class="form-select purchase-product" style="display: none;"></select>
        <div class="search-container"></div>
      </div>
    </td>
    <td><input type="number" class="form-control purchase-qty" value="1" min="1"></td>
    <td><input type="number" class="form-control purchase-cost" value="0" min="0" step="0.01"></td>
    <td><input type="number" class="form-control purchase-vat" value="0.19" min="0" step="0.01"></td>
    <td><button class="btn btn-sm btn-outline-danger" onclick="this.closest('tr').remove()">Eliminar</button></td>`;
  tbody.appendChild(tr);
  
  // Configurar búsqueda para el nuevo select
  const selectEl = tr.querySelector('.purchase-product');
  const searchContainer = tr.querySelector('.search-container');
  
  // Crear input de búsqueda
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.className = 'form-control';
  searchInput.placeholder = 'Buscar producto...';
  
  // Crear dropdown
  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-menu w-100';
  dropdown.style.display = 'none';
  dropdown.style.position = 'absolute';
  dropdown.style.zIndex = '1000';
  
  searchContainer.appendChild(searchInput);
  searchContainer.appendChild(dropdown);
  
  // Configurar búsqueda
  let searchTimeout = null;
  searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      searchProductsForPurchaseRow(e.target.value, selectEl, dropdown);
    }, 300);
  });
  
  // Mostrar/ocultar dropdown
  searchInput.addEventListener('focus', () => {
    if (dropdown.children.length > 0) {
      dropdown.style.display = 'block';
    }
  });
  
  // Cargar productos iniciales
  populateProductSelect(selectEl);
}

async function populateProductSelect(selectEl){
  let products = await fetchJSON('/api/products');
  selectEl.innerHTML = '';
  products.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `${p.sku} — ${p.name}`;
    selectEl.appendChild(opt);
  });
}

async function savePurchase(){
  const supplier = {
    name: document.getElementById('supName').value.trim(),
    phone: document.getElementById('supPhone').value.trim(),
    email: document.getElementById('supEmail').value.trim(),
    address: document.getElementById('supAddress').value.trim()
  };
  const rows = document.querySelectorAll('#purchaseItems tbody tr');
  if (rows.length === 0){ alert("Agrega al menos un ítem."); return; }
  const items = [];
  rows.forEach(r => {
    items.push({
      product_id: parseInt(r.querySelector('.purchase-product').value),
      quantity: parseInt(r.querySelector('.purchase-qty').value || '0'),
      unit_cost: parseFloat(r.querySelector('.purchase-cost').value || '0'),
      vat_rate: parseFloat(r.querySelector('.purchase-vat').value || '0.19')
    });
  });
  const body = {supplier, items, notes: document.getElementById('purchaseNotes').value || ''};
  try {
    let res = await fetchJSON('/api/purchases', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('purchaseResult').innerHTML = `<div class="alert alert-success">Compra <strong>${res.code}</strong> guardada. Total: ${money(res.total)}</div>`;
    // limpiar
    document.querySelector('#purchaseItems tbody').innerHTML = '';
    await refreshProducts();
    await refreshAlerts();
  } catch (e){
    alert("Error guardando compra: " + e.message);
  }
}

// --- Ventas: Remisiones / Facturas ---
function addSaleRow(tbodyId){
  const tbody = document.querySelector('#' + tbodyId + ' tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><select class="form-select sale-product"></select></td>
    <td><input type="number" class="form-control sale-qty" value="1" min="1"></td>
    <td><input type="number" class="form-control sale-price" value="0" min="0" step="0.01"></td>
    <td><input type="number" class="form-control sale-vat" value="0.19" min="0" step="0.01"></td>
    <td><button class="btn btn-sm btn-outline-danger" onclick="this.closest('tr').remove()">Eliminar</button></td>`;
  tbody.appendChild(tr);
  populateProductSelect(tr.querySelector('.sale-product'));
}

async function saveRemission(){
  const customer = {
    name: document.getElementById('remName').value.trim(),
    document_number: document.getElementById('remDoc').value.trim(),
    phone: document.getElementById('remPhone').value.trim(),
    email: document.getElementById('remEmail').value.trim(),
    address: document.getElementById('remAddr').value.trim()
  };
  const rows = document.querySelectorAll('#remissionItems tbody tr');
  if (rows.length === 0){ alert("Agrega al menos un ítem."); return; }
  const items = [];
  rows.forEach(r => {
    items.push({
      product_id: parseInt(r.querySelector('.sale-product').value),
      quantity: parseInt(r.querySelector('.sale-qty').value || '0'),
      unit_price: parseFloat(r.querySelector('.sale-price').value || '0'),
      vat_rate: parseFloat(r.querySelector('.sale-vat').value || '0.19')
    });
  });
  const body = {customer, items};
  const mdays = parseInt(document.getElementById('remMaintenanceDays').value || '0');
  if (mdays > 0) body.maintenance_days = mdays;
  body.payment_method = document.getElementById('remPaymentMethod').value;
  try {
    let res = await fetchJSON('/api/remissions', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('remissionResult').innerHTML = `<div class="alert alert-success">Remisión <strong>${res.number}</strong> creada. Total: ${money(res.total)} — <a href="/remission/${res.id}" target="_blank">Ver</a></div>`;
    document.querySelector('#remissionItems tbody').innerHTML = '';
    await refreshProducts();
    await refreshAlerts();
  } catch (e){
    alert("Error creando remisión: " + e.message);
  }
}

async function saveInvoice(){
  const customer = {
    name: document.getElementById('invName').value.trim(),
    document_number: document.getElementById('invDoc').value.trim(),
    phone: document.getElementById('invPhone').value.trim(),
    email: document.getElementById('invEmail').value.trim(),
    address: document.getElementById('invAddr').value.trim()
  };
  const rows = document.querySelectorAll('#invoiceItems tbody tr');
  if (rows.length === 0){ alert("Agrega al menos un ítem."); return; }
  const items = [];
  rows.forEach(r => {
    items.push({
      product_id: parseInt(r.querySelector('.sale-product').value),
      quantity: parseInt(r.querySelector('.sale-qty').value || '0'),
      unit_price: parseFloat(r.querySelector('.sale-price').value || '0'),
      vat_rate: parseFloat(r.querySelector('.sale-vat').value || '0.19')
    });
  });
  const body = {customer, items};
  const mdays = parseInt(document.getElementById('invMaintenanceDays').value || '0');
  if (mdays > 0) body.maintenance_days = mdays;
  body.payment_method = document.getElementById('invPaymentMethod').value;
  try {
    let res = await fetchJSON('/api/invoices', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('invoiceResult').innerHTML = `<div class="alert alert-success">Factura <strong>${res.number}</strong> creada. Total: ${money(res.total)} — <a href="/invoice/${res.id}" target="_blank">Ver</a></div>`;
    document.querySelector('#invoiceItems tbody').innerHTML = '';
    await refreshProducts();
    await refreshAlerts();
  } catch (e){
    alert("Error creando factura: " + e.message);
  }
}

// ------ Funcionalidad de búsqueda de productos ------
let searchTimeout = null;

function createProductSearchInput(selectElement, placeholder = "Buscar producto...") {
  // Crear contenedor para el input de búsqueda
  const searchContainer = document.createElement('div');
  searchContainer.className = 'position-relative';
  
  // Crear input de búsqueda
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.className = 'form-control';
  searchInput.placeholder = placeholder;
  searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      searchProducts(e.target.value, selectElement);
    }, 300);
  });
  
  // Crear dropdown de resultados
  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-menu w-100';
  dropdown.style.display = 'none';
  dropdown.id = 'searchDropdown_' + Math.random().toString(36).substr(2, 9);
  
  // Reemplazar el select con el contenedor de búsqueda
  selectElement.parentNode.insertBefore(searchContainer, selectElement);
  searchContainer.appendChild(searchInput);
  searchContainer.appendChild(dropdown);
  selectElement.style.display = 'none';
  
  // Mostrar/ocultar dropdown
  searchInput.addEventListener('focus', () => {
    if (dropdown.children.length > 0) {
      dropdown.style.display = 'block';
    }
  });
  
  document.addEventListener('click', (e) => {
    if (!searchContainer.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
  
  return { searchInput, dropdown };
}

async function searchProducts(query, selectElement) {
  if (!query || query.length < 2) {
    return;
  }
  
  try {
    const response = await fetch(`/api/products/search?q=${encodeURIComponent(query)}`);
    const products = await response.json();
    
    const dropdown = document.querySelector(`#searchDropdown_${selectElement.id}`);
    if (!dropdown) return;
    
    dropdown.innerHTML = '';
    
    if (products.length === 0) {
      dropdown.innerHTML = '<div class="dropdown-item text-muted">No se encontraron productos</div>';
    } else {
      products.forEach(product => {
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.style.cursor = 'pointer';
        item.innerHTML = `
          <div class="fw-bold">${product.sku}</div>
          <div class="small text-muted">${product.name}</div>
          <div class="small">Stock: ${product.current_stock} | Precio: ${money(product.price)}</div>
        `;
        
        item.addEventListener('click', () => {
          selectElement.value = product.id;
          selectElement.dispatchEvent(new Event('change'));
          dropdown.style.display = 'none';
        });
        
        dropdown.appendChild(item);
      });
    }
    
    dropdown.style.display = 'block';
  } catch (error) {
    console.error('Error buscando productos:', error);
  }
}

// Modificar la función addSaleRow para incluir búsqueda
function addSaleRow(tbodyId){
  const tbody = document.querySelector('#' + tbodyId + ' tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>
      <div class="position-relative">
        <select class="form-select sale-product" style="display: none;"></select>
        <div class="search-container"></div>
      </div>
    </td>
    <td><input type="number" class="form-control sale-qty" value="1" min="1"></td>
    <td><input type="number" class="form-control sale-price" value="0" min="0" step="0.01"></td>
    <td><input type="number" class="form-control sale-vat" value="0.19" min="0" step="0.01"></td>
    <td><button class="btn btn-sm btn-outline-danger" onclick="this.closest('tr').remove()">Eliminar</button></td>`;
  tbody.appendChild(tr);
  
  // Configurar búsqueda para el nuevo select
  const selectEl = tr.querySelector('.sale-product');
  const searchContainer = tr.querySelector('.search-container');
  
  // Crear input de búsqueda
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.className = 'form-control';
  searchInput.placeholder = 'Buscar producto...';
  
  // Crear dropdown
  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-menu w-100';
  dropdown.style.display = 'none';
  dropdown.style.position = 'absolute';
  dropdown.style.zIndex = '1000';
  
  searchContainer.appendChild(searchInput);
  searchContainer.appendChild(dropdown);
  
  // Configurar búsqueda
  let searchTimeout = null;
  searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      searchProductsForRow(e.target.value, selectEl, dropdown);
    }, 300);
  });
  
  // Mostrar/ocultar dropdown
  searchInput.addEventListener('focus', () => {
    if (dropdown.children.length > 0) {
      dropdown.style.display = 'block';
    }
  });
  
  // Cargar productos iniciales
  populateProductSelect(selectEl);
}

async function searchProductsForRow(query, selectElement, dropdown) {
  if (!query || query.length < 2) {
    dropdown.innerHTML = '';
    dropdown.style.display = 'none';
    return;
  }
  
  try {
    const response = await fetch(`/api/products/search?q=${encodeURIComponent(query)}`);
    const products = await response.json();
    
    dropdown.innerHTML = '';
    
    if (products.length === 0) {
      dropdown.innerHTML = '<div class="dropdown-item text-muted">No se encontraron productos</div>';
    } else {
      products.forEach(product => {
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.style.cursor = 'pointer';
        item.innerHTML = `
          <div class="fw-bold">${product.sku}</div>
          <div class="small text-muted">${product.name}</div>
          <div class="small">Stock: ${product.current_stock} | Precio: ${money(product.price)}</div>
        `;
        
        item.addEventListener('click', () => {
          selectElement.value = product.id;
          selectElement.dispatchEvent(new Event('change'));
          dropdown.style.display = 'none';
          
          // Llenar automáticamente el precio
          const priceInput = selectElement.closest('tr').querySelector('.sale-price');
          if (priceInput) {
            priceInput.value = product.price;
          }
        });
        
        dropdown.appendChild(item);
      });
    }
    
    dropdown.style.display = 'block';
  } catch (error) {
    console.error('Error buscando productos:', error);
  }
}

async function searchProductsForPurchaseRow(query, selectElement, dropdown) {
  if (!query || query.length < 2) {
    dropdown.innerHTML = '';
    dropdown.style.display = 'none';
    return;
  }
  
  try {
    const response = await fetch(`/api/products/search?q=${encodeURIComponent(query)}`);
    const products = await response.json();
    
    dropdown.innerHTML = '';
    
    if (products.length === 0) {
      dropdown.innerHTML = '<div class="dropdown-item text-muted">No se encontraron productos</div>';
    } else {
      products.forEach(product => {
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.style.cursor = 'pointer';
        item.innerHTML = `
          <div class="fw-bold">${product.sku}</div>
          <div class="small text-muted">${product.name}</div>
          <div class="small">Stock: ${product.current_stock} | Precio: ${money(product.price)}</div>
        `;
        
        item.addEventListener('click', () => {
          selectElement.value = product.id;
          selectElement.dispatchEvent(new Event('change'));
          dropdown.style.display = 'none';
          
          // Llenar automáticamente el costo unitario
          const costInput = selectElement.closest('tr').querySelector('.purchase-cost');
          if (costInput) {
            costInput.value = product.price;
          }
        });
        
        dropdown.appendChild(item);
      });
    }
    
    dropdown.style.display = 'block';
  } catch (error) {
    console.error('Error buscando productos:', error);
  }
}
