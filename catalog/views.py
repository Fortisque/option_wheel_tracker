from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, reverse, redirect
from django.urls import reverse_lazy
from django.views import generic
from django.contrib.auth.models import User
from django.db.models import Count, Q, Sum, F, fields
from django.db.models.functions import Coalesce, Round, Cast, Power

from catalog.forms import OptionPurchaseForm, StockTickerForm, SignupForm, OptionWheelForm, AccountForm
from catalog.models import Account, OptionPurchase, StockTicker, OptionWheel

from datetime import timedelta, datetime
from collections import defaultdict 

from .option_price_computation import (
    get_current_price,
    get_put_stats_for_ticker,
    get_call_stats_for_option_wheel,
    get_earnings,
    compute_annualized_rate_of_return,
    BUSINESS_DAYS_IN_YEAR
)
from .business_day_count import busday_count_inclusive
from .schedule_async import schedule_global_put_comparison_async, GLOBAL_PUT_CACHE_KEY

import numpy
import pandas
import json

from django.views.decorators.cache import cache_page
from django.core.cache import cache

ALL_VIEWS_PAGE_CACHE_IN_SECONDS = 60

def _get_today():
    return datetime.now().date()

def _get_next_friday():
    today = _get_today()
    return today + timedelta((3 - today.weekday()) % 7 + 1)

def _get_last_trading_day():
    now = datetime.now()
    today = _get_today()
    if now.hour < settings.MARKET_OPEN_HOUR:
        today -= timedelta(days=1)
    while today.weekday() > 4:
        today -= timedelta(days=1)
    return today

def _inject_earnings(context, stockticker_name):
    earnings = get_earnings(stockticker_name)
    if earnings:
        variant = 'warning'
        if earnings <= _get_next_friday():
            variant = 'danger'
        else:
            days = (earnings - _get_today()).days
            if days > 14:
                variant = 'success'
        context['earnings'] = earnings
        context['earnings_variant'] = variant


class PageTitleMixin(object):
  def get_page_title(self, context):
    return getattr(self, "page_title", "Option Wheel")

  def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["page_title"] = self.get_page_title(context)
    return context


def index(request):
    return render(request, 'index.html')

def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('signup-complete')
    else:
        form = SignupForm()
    return render(request, 'signup.html', {'form': form})

def signup_complete(request):
    return render(request, 'signup_complete.html')

def global_put_comparison(request):
    context = {}
    cached_result = cache.get(GLOBAL_PUT_CACHE_KEY)
    if cached_result is not None:
        context['put_stats'] = cached_result
        return render(request, 'global_put_comparison.html', context=context)
    try:
        schedule_global_put_comparison_async()
    except:
        context['permanently_unavailable'] = True
    context['unavailable'] = True
    return render(request, 'global_put_comparison.html', context=context)


# StockTicker views
class StockTickerListView(PageTitleMixin, generic.ListView):
    page_title = "Tickers"
    model = StockTicker
 
class StockTickerDetailView(PageTitleMixin, generic.DetailView):
    model = StockTicker

    def get_context_data(self, **kwargs):
        context = super(StockTickerDetailView, self).get_context_data(**kwargs)
        num_wheels = OptionWheel.objects.filter(stock_ticker=self.object.id).count()
        _inject_earnings(context, self.object.name)
        result = get_put_stats_for_ticker(self.object)
        context['put_stats'] = sorted(result['put_stats'], key=lambda put: put['annualized_rate_of_return_decimal'], reverse=True)
        context['current_price'] = result['current_price']
        context['num_wheels'] = num_wheels
        return context

    def get_page_title(self, context):
        return self.object.name


class StockTickerCreate(PageTitleMixin, generic.edit.CreateView):
    page_title = "Create Ticker"
    model = StockTicker
    form_class = StockTickerForm
    success_url = reverse_lazy('tickers')


class StockTickerUpdate(PageTitleMixin, generic.edit.UpdateView):
    model = StockTicker
    form_class = StockTickerForm
    success_url = reverse_lazy('tickers')

    def get_page_title(self, context):
        return f"Update {self.object.name} Ticker"

class StockTickerDelete(PageTitleMixin, generic.edit.DeleteView):
    model = StockTicker
    success_url = reverse_lazy('tickers')

    def get_page_title(self, context):
        return f"Delete {self.object.name} Ticker"

# Account views
class MyAccountsListView(PageTitleMixin, LoginRequiredMixin, generic.ListView):
    page_title = "My Accounts"
    model = Account

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user)

class AccountCreate(PageTitleMixin, LoginRequiredMixin, generic.edit.CreateView):
    page_title = "Create Account"
    model = Account
    form_class = AccountForm
    success_url = reverse_lazy('my-accounts')

    def get_initial(self):
        user = self.request.user
        return {
            'user': user
        }

class AccountUpdate(PageTitleMixin, LoginRequiredMixin, generic.edit.UpdateView):
    model = Account
    form_class = AccountForm
    success_url = reverse_lazy('my-accounts')

    def get_page_title(self, context):
        return f"Update {self.object.name} Account"

class AccountDelete(PageTitleMixin, LoginRequiredMixin, generic.edit.DeleteView):
    model = Account
    success_url = reverse_lazy('my-accounts')

    def get_page_title(self, context):
        return f"Delete {self.object.name} Account"

class AccountDetailView(PageTitleMixin, LoginRequiredMixin, generic.DetailView):
    model = Account

    def get_page_title(self, context):
        return f"{self.object.name} Account"

# OptionWheel views
@login_required
def my_active_wheels(request):
    user = request.user
        # .select_related('account') \
        # .select_related('user') \
        # .select_related('stock_ticker') \
        # .prefetch_related('option_purchases') \
    wheels = OptionWheel.objects \
        .filter(user=user, is_active=True)
    for wheel in wheels:
        print("adding purchase data", wheel)
        wheel.add_purchase_data()
    context = {'wheel_user': user}
    context["wheels"] = wheels
    context["can_edit"] = True
    context["page_title"] = "My Active Wheels"
    return render(request, 'active_wheels.html', context=context)

def active_wheels(request, pk):
    user = User.objects.get(pk=pk)
    wheels = OptionWheel.objects.filter(user=user, is_active=True)
    for wheel in wheels:
        wheel.add_purchase_data()
    context = {'wheel_user': user}
    context["wheels"] = wheels
    context["can_edit"] = request.user == user
    context["page_title"] = f"{user}'s Active Wheels"
    return render(request, 'active_wheels.html', context=context)

@login_required
def my_completed_wheels(request):
    user = request.user
    wheels = OptionWheel.objects.filter(user=user, is_active=False)
    for wheel in wheels:
        wheel.add_purchase_data(fetch_price=False)
    context = {'wheel_user': user}
    context["wheels"] = wheels
    context["page_title"] = "My Completed Wheels"
    return render(request, 'completed_wheels.html', context=context)

def completed_wheels(request, pk):
    user = User.objects.get(pk=pk)
    wheels = OptionWheel.objects.filter(user=user, is_active=False)
    for wheel in wheels:
        wheel.add_purchase_data(fetch_price=False)
    context = {'wheel_user': user}
    context["wheels"] = wheels
    context["page_title"] = f"{user}'s Completed Wheels"
    return render(request, 'completed_wheels.html', context=context)

@cache_page(ALL_VIEWS_PAGE_CACHE_IN_SECONDS)
def all_active_wheels(request):
    context = {}
    wheels = OptionWheel.objects.filter(is_active=True)
    for wheel in wheels:
        wheel.add_purchase_data()
    context["wheels"] = wheels
    context["page_title"] = "All Active Wheels"
    return render(request, 'all_active_wheels.html', context=context)


@cache_page(ALL_VIEWS_PAGE_CACHE_IN_SECONDS)
def all_completed_wheels(request):
    context = {}
    wheels = OptionWheel.objects.select_related('account').select_related('user').select_related('stock_ticker').filter(is_active=False)
    for wheel in wheels:
        wheel.add_purchase_data(fetch_price=False)
    context["wheels"] = wheels
    context["page_title"] = "All Completed Wheels"
    return render(request, 'all_completed_wheels.html', context=context)

@cache_page(ALL_VIEWS_PAGE_CACHE_IN_SECONDS)
def todays_active_wheels(request):
    date = _get_last_trading_day()
    context = {}
    wheels = OptionWheel.objects.filter(is_active=True)
    todays_wheels = []
    for wheel in wheels:
        last_purchase = wheel.get_last_option_purchase()
        if last_purchase:
            if date == last_purchase.purchase_date.date():
                wheel.add_purchase_data()
                todays_wheels.append(wheel)
    context["wheels"] = todays_wheels
    context["date"] = date
    context["page_title"] = "Today's Active Wheels"
    return render(request, 'todays_active_wheels.html', context=context)


class OptionWheelDetailView(PageTitleMixin, generic.DetailView):
    model = OptionWheel
    context_object_name = 'wheel'

    def get_context_data(self, **kwargs):
        context = super(OptionWheelDetailView, self).get_context_data(**kwargs)
        option_wheel = OptionWheel.objects.get(pk=self.kwargs.get('pk'))

        option_wheel.add_purchase_data()
        context["wheel_data"] = option_wheel
        context["can_edit"] = option_wheel.user == self.request.user
        return context

    def get_page_title(self, context):
        return self.object


@login_required
def complete_wheel(request, pk):
    option_wheel = OptionWheel.objects.get(pk=pk)
    purchases = option_wheel.get_all_option_purchases()

    option_wheel.is_active = False
    if purchases:
        last_purchase = option_wheel.get_last_option_purchase()
        first_purchase = option_wheel.get_first_option_purchase()

        premiums = sum(purchase.premium for purchase in purchases)
        profit = premiums + last_purchase.strike - first_purchase.strike

        bus_days = busday_count_inclusive(
            first_purchase.purchase_date.date(),
            last_purchase.expiration_date,
        )
        puts = [p for p in purchases if p.call_or_put == 'P']
        max_collateral = max(p.strike for p in puts)

        option_wheel.total_profit = profit
        option_wheel.total_days_active = bus_days
        option_wheel.collatoral = max_collateral
    option_wheel.save()
    if 'next' in request.GET:
        return redirect(request.GET['next'])
    return redirect('wheel-detail', pk=pk)

@login_required
def reactivate_wheel(request, pk):
    option_wheel = OptionWheel.objects.get(pk=pk)
    option_wheel.is_active = True
    option_wheel.total_profit = None
    option_wheel.total_days_active = None
    option_wheel.collatoral = None
    option_wheel.save()
    return redirect('wheel-detail', pk=pk)

class OptionWheelCreate(PageTitleMixin, generic.edit.CreateView):
    page_title = "Create Wheel"
    model = OptionWheel
    form_class = OptionWheelForm

    def get_initial(self):
        user = self.request.user
        return {
            'user': user, 
            'is_active': True
        }
    
    def get_form_kwargs(self):
        kwargs = super(OptionWheelCreate, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs 

    def get_success_url(self):
        return reverse('purchase-create', args=[str(self.object.id)])

class OptionWheelUpdate(PageTitleMixin, generic.edit.UpdateView):
    model = OptionWheel
    form_class = OptionWheelForm

    def get_form_kwargs(self):
        kwargs = super(OptionWheelUpdate, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs 

    def get_success_url(self):
        return reverse('wheel-detail', args=[str(self.object.id)])

    def get_page_title(self, context):
        return f"Update {self.object}"

class OptionWheelDelete(PageTitleMixin, LoginRequiredMixin, generic.edit.DeleteView):
    model = OptionWheel
    success_url = reverse_lazy('my-active-wheels')

    def get_page_title(self, context):
        return f"Delete {self.object}"


# OptionPurchase views
class OptionPurchaseDetailView(PageTitleMixin, LoginRequiredMixin, generic.DetailView):
    model = OptionPurchase

    def get_context_data(self, **kwargs):
        context = super(OptionPurchaseDetailView, self).get_context_data(**kwargs)
        context["can_edit"] = self.object.user == self.request.user
        return context

    def get_page_title(self, context):
        return f"Option: {self.object}"

class OptionPurchaseCreate(PageTitleMixin, LoginRequiredMixin, generic.edit.CreateView):
    page_title = "Create Option"
    model = OptionPurchase
    form_class = OptionPurchaseForm

    def get_initial(self):
        user = self.request.user
        option_wheel = OptionWheel.objects.get(pk=self.kwargs.get('wheel_id'))

        price_at_date = None
        current_price = get_current_price(option_wheel.stock_ticker.name)
        if current_price:
            price_at_date = round(current_price, 2)

        first_strike = None
        call_or_put = 'P'
        first_option_purchase = option_wheel.get_first_option_purchase()
        if first_option_purchase:
            call_or_put = 'C'
            first_strike = first_option_purchase.strike
        now = datetime.now()
        return {
            'user': user, 
            'option_wheel': option_wheel,
            'purchase_date': now,
            'expiration_date': _get_next_friday(),
            'call_or_put': call_or_put,
            'strike': first_strike,
            'price_at_date': price_at_date,
        }

    def get_context_data(self, **kwargs):
        context = super(OptionPurchaseCreate, self).get_context_data(**kwargs)
        option_wheel = OptionWheel.objects.get(pk=self.kwargs.get('wheel_id'))
        context['option_wheel'] = option_wheel
        context['cost_basis'] = option_wheel.get_cost_basis()
        _inject_earnings(context, option_wheel.stock_ticker.name)
        first_purchase = option_wheel.get_first_option_purchase()
        if first_purchase is not None:
            last_purchase = option_wheel.get_last_option_purchase()
            days_active_so_far = busday_count_inclusive(
                first_purchase.purchase_date.date(),
                last_purchase.expiration_date,
            )
            call_stats = get_call_stats_for_option_wheel(option_wheel.stock_ticker, days_active_so_far, option_wheel.get_revenue(), collateral=first_purchase.strike)
            context['call_stats'] = call_stats['call_stats']
        return context

    def get_success_url(self):
        wheel_id = self.kwargs.get('wheel_id')
        return reverse('wheel-detail', args=[str(wheel_id)])

class OptionPurchaseUpdate(PageTitleMixin, LoginRequiredMixin, generic.edit.UpdateView):
    model = OptionPurchase
    form_class = OptionPurchaseForm

    def get_context_data(self, **kwargs):
        context = super(OptionPurchaseUpdate, self).get_context_data(**kwargs)
        context['option_wheel'] = self.object
        return context

    def get_page_title(self, context):
        return f"Update Option: {self.object}"

class OptionPurchaseDelete(PageTitleMixin, LoginRequiredMixin, generic.edit.DeleteView):
    model = OptionPurchase

    def get_success_url(self):
        wheel_id = self.kwargs.get('wheel_id')
        return reverse('wheel-detail', args=[str(wheel_id)])

    def get_page_title(self, context):
        return f"Delete Option: {self.object}"

# Total Profit Views
@login_required
def my_total_profit(request):
    user = request.user
    wheels = OptionWheel.objects.filter(user=user, is_active=False)
    context = _setup_context_for_total_profit(wheels, {'profit_user': user, 'page_title': 'My Profit'})
    return render(request, 'total_profit.html', context=context)


def total_profit(request, pk):
    user = User.objects.get(pk=pk)
    wheels = OptionWheel.objects.filter(user=user, is_active=False)
    context = _setup_context_for_total_profit(wheels, {'profit_user': user, 'page_title': f"{user}'s Profit"})
    return render(request, 'total_profit.html', context=context)


def _setup_context_for_total_profit(wheels, context):
    total_profit = 0
    total_collateral = 0
    sum_days_weighted_by_collateral = 0
    wheel_count = 0
    no_quantity_wheel_count = 0
    collateral_on_the_line_per_day = defaultdict(int)
    profit_per_day = defaultdict(int)
    for wheel in wheels:
        wheel_collateral = wheel.collateral * wheel.quantity
        wheel_profit = wheel.total_profit * wheel.quantity
        total_profit += wheel_profit
        total_collateral += wheel_collateral
        wheel_count += wheel.quantity
        sum_days_weighted_by_collateral += wheel.total_days_active * wheel_collateral
        no_quantity_wheel_count += 1
        profit_per_day[wheel.get_expiration_date().strftime('%Y-%m-%d')] += float(wheel_profit)
        for day in pandas.bdate_range(wheel.get_open_date(), wheel.get_expiration_date()):
            collateral_on_the_line_per_day[day.strftime('%Y-%m-%d')] += float(wheel_collateral)
    context["total_profit"] = total_profit
    context["total_collateral"] = total_collateral
    context["total_profit_dollars"] = total_profit * 100
    context["total_collateral_dollars"] = total_collateral * 100
    wheel_count = max([wheel_count, 1]) # avoid divide by 0
    total_collateral = max([total_collateral, 1])
    total_days_active_average = (float) (sum_days_weighted_by_collateral / total_collateral)
    context["total_days_active_average"] = total_days_active_average
    return_percentage = (float) (total_profit / total_collateral)
    context["return_percentage"] = return_percentage
    context["total_wheel_count"] = wheel_count
    context["no_quantity_wheel_count"] = no_quantity_wheel_count
    context["collateral_on_the_line_per_day"] = json.dumps(list(collateral_on_the_line_per_day.items()))
    context["profit_per_day"] = json.dumps(list(profit_per_day.items()))
    context["max_collateral"] = max(collateral_on_the_line_per_day.values() or [0])
    context["annualized_rate_of_return_decimal"] = compute_annualized_rate_of_return(return_percentage, 1, total_days_active_average)
    return context


# User views
class UserListView(PageTitleMixin, generic.ListView):
    page_title = "User Overview"
    model = User

    def get_queryset(self):
        active = Count("optionwheel", filter=Q(optionwheel__is_active=True))
        completed = Count("optionwheel", filter=Q(optionwheel__is_active=False))
        profit = Sum(F("optionwheel__total_profit") * F("optionwheel__quantity"),
            filter=Q(optionwheel__is_active=False),
            output_field=fields.DecimalField())
        collateral = Sum(F("optionwheel__collatoral") * F("optionwheel__quantity"),
            filter=Q(optionwheel__is_active=False),
            output_field=fields.DecimalField())
        total_wheels = Sum(F("optionwheel__quantity"),
            filter=Q(optionwheel__is_active=False))
        total_days_active_weighted_by_collateral = Sum(F("optionwheel__total_days_active") * F("optionwheel__quantity") * F("optionwheel__collatoral"),
            filter=Q(optionwheel__is_active=False))
        average_days = Cast(total_days_active_weighted_by_collateral, fields.FloatField()) / Cast(collateral, fields.FloatField())
        annualized_rate_of_return = Power(1 + profit / collateral, BUSINESS_DAYS_IN_YEAR / Coalesce(average_days, 252))
        users = User.objects.annotate(
            active=active,
            completed=completed, 
            profit=Round(100 * Coalesce(profit, 0)),
            collateral=Round(100 * Coalesce(collateral, 0)),
            return_percentage=Coalesce(profit / collateral, 0),
            total_wheels=Coalesce(total_wheels, 0),
            average_days = Coalesce(average_days, 0),
            annualized_rate_of_return=Coalesce(annualized_rate_of_return, 0),
        )
        return users
